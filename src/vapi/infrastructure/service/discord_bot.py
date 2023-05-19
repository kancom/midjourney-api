import asyncio
import itertools
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

import aiohttp
import vapi.infrastructure.counters as cnt
from discord import Client, Intents
from discord.message import Message
from loguru import logger
from pydantic import BaseModel
from vapi.application import (Command, IQueueService, Outcome, Priority,
                              RouteLabel, TaskDeliverable)
from vapi.application.domain.task import GenerateTask, VariationTask

logger.remove()
fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> {extra[human]} - <level>{message}</level>"
logger.add(sys.stderr, format=fmt)


class NotAuthorized(Exception):
    pass


class BadRequest(Exception):
    pass


class TooManyRequest(Exception):
    pass


class GenericRequestError(Exception):
    pass


class Bot(Client):
    url = "https://discord.com/api/v9/interactions"
    MJ_BOT_ID = 936929561302675456
    max_proc_time: timedelta = timedelta(minutes=5)
    capacity = {True: 10, False: 1}
    progress_str = re.compile(r"\(([0-9]+)%\)")
    mode_str = re.compile(r"\(([a-z]+), ([a-z]+)\)")
    clean_str = re.compile(r"[^A-Za-z]")
    percent_re = r"\d+%"
    max_evictions = 5

    class BotInitCont(BaseModel):
        bot_id: int
        bot_pool: str
        human_name: str
        high_priority: bool = False
        channel_id: str
        server_id: str
        user_access_token: str
        bot_access_token: str
        proxy: Optional[str] = None

        def __hash__(self) -> int:
            return hash(
                tuple(getattr(self, f) for f in self.schema()["properties"].keys())
            )

    def __init__(
        self,
        *,
        init_cont: BotInitCont,
        queue_service: IQueueService,
        **options: Any,
    ) -> None:
        super().__init__(intents=Intents.all(), **options)
        self._bot_id = init_cont.bot_id
        self._bot_pool = init_cont.bot_pool
        self._human_name = init_cont.human_name
        self._high_priority = init_cont.high_priority
        self._channel_id = init_cont.channel_id
        self._server_id = init_cont.server_id
        self._user_access_token = init_cont.user_access_token
        self._bot_access_token = init_cont.bot_access_token
        self._proxy = init_cont.proxy
        if self._proxy and self._proxy.count(":") == 3:
            self._proxy = "{2}:{3}@{0}:{1}".format(*self._proxy.split(":"))

        self._queue_service = queue_service
        self._current_tasks: Dict[UUID, datetime] = {}
        self._logger = logger.bind(human=self._human_name)
        self._logger.debug(self._proxy)
        self._eviction_count = 0
        self.max_task_age = (
            timedelta(minutes=10) if self._high_priority else timedelta(minutes=13)
        )

    @property
    def identity(self) -> str:
        return self._human_name or str(self._bot_id)

    async def _worker(self):
        self._logger.info(f"Bot id {self._bot_id} worker starting")
        task_id = None
        while True:
            try:
                await asyncio.sleep(1)
                cnt.QUEUE_LEN.labels(self._human_name, self._high_priority).set(
                    len(self._current_tasks)
                )
                if len(self._current_tasks) >= self.capacity[self._high_priority]:
                    now = datetime.utcnow()
                    ev = [
                        k
                        for k, v in self._current_tasks.items()
                        if now - v > self.max_task_age
                    ]
                    if len(ev) and not self._high_priority:
                        self.max_task_age = timedelta(minutes=10)
                        self._eviction_count += 1
                        self._logger.warning(ev)
                    for t in ev:
                        cnt.REQ_BY_METHOD_ERROR.labels(
                            self._human_name, "unknown", "TaskEviction"
                        ).inc()
                        t = await self._queue_service.get_task_by_id(t)
                        t.status = Outcome.Failure
                        await self._queue_service.put_task(t)

                    self._current_tasks = {
                        k: v
                        for k, v in self._current_tasks.items()
                        if now - v < self.max_task_age
                    }
                    continue
                if self._eviction_count > self.max_evictions:
                    self._logger.critical(
                        f"Exit. Too many evictions {self._eviction_count}"
                    )
                    break
                for p, bot in itertools.product(Priority, (self._bot_id, None)):
                    if (
                        self._high_priority and p in (Priority.Low, Priority.Normal)
                    ) or (
                        not self._high_priority and p in (Priority.VIP, Priority.High)
                    ):
                        continue
                    route_label = RouteLabel(
                        priority=p, bot_id=bot, bot_pool=self._bot_pool
                    )
                    task_id = await self._queue_service.get_next_task_id(route_label)
                    if task_id is not None:
                        self._logger.debug(f"{task_id} from {p},{bot},{self._bot_pool}")
                        break
                else:
                    cnt.IDLE.labels(self._human_name).inc()
                    continue

                task = await self._queue_service.get_task_by_id(task_id)
                if task.status != Outcome.New:
                    raise ValueError(f"task {task_id} has non New status {task.status}")
                if task_id in self._current_tasks:
                    self._logger.warning(
                        f"Collision {task} is in {self._current_tasks}"
                    )
                self._current_tasks[task_id] = datetime.utcnow()
                self._logger.debug(
                    f"{task_id},{list(self._current_tasks.keys())}, {task.params}"
                )
                try:
                    cnt.REQ_BY_METHOD.labels(self._human_name, task.command.value).inc()
                    if task.command == Command.New and isinstance(
                        task.params, GenerateTask
                    ):
                        await self.send_prompt(task.params.prompt)
                        task.status = Outcome.Pending
                        task.route_label.bot_id = self._bot_id
                        await self._queue_service.put_task(task)
                    elif task.command == Command.Variation and isinstance(
                        task.params, VariationTask
                    ):
                        task = await self._queue_service.get_task_by_id(task.uuid)
                        if task.discord_msg_id is None or task.deliverable is None:
                            task.status = Outcome.Failure
                            await self._queue_service.put_task(task)
                            raise ValueError(f"invalid message flow {task}")
                        await self.request_variations(
                            task.params.position.value,
                            dscrd_msg_id=task.discord_msg_id,
                            dscrd_img_nm=task.deliverable.filename,
                        )
                        task.status == Outcome.Pending
                        task.progress = 0
                        await self._queue_service.put_task(task)
                except Exception as ex:
                    cnt.REQ_BY_METHOD_ERROR.labels(
                        self._human_name, task.command.value, str(type(ex))
                    ).inc()
                    self._logger.error(f"{ex} {task}")
                    if task.command == Command.New:
                        await self._queue_service.push_back_task_id(
                            str(task.uuid), task.route_label
                        )
                    if task_id is not None and task_id in self._current_tasks:
                        del self._current_tasks[task_id]
                    await asyncio.sleep(30)
                    raise
            except Exception as ex:
                self._logger.error(ex)
        await self.close()

    async def start(self):
        t = None
        try:
            t = asyncio.create_task(self._worker())
            self._logger.info(f"Bot id {self._bot_id} discord coroutine starting")
            if self._high_priority:
                await self.set_fast_mode()
            await super().start(self._bot_access_token)
        except asyncio.exceptions.CancelledError:
            if t is not None:
                t.cancel()
            self._logger.warning("cancelled")
            return
        except Exception as ex:
            self._logger.error(ex)

    async def _ensure_task(self, message: Message):
        # result of new generation?
        try:
            task_id = await self._queue_service.lookup_task_by_msg(message.id)
            task = await self._queue_service.get_task_by_id(task_id)
            if (
                task.command == Command.New
                and task.params.prompt
                and task.params.prompt != message.content
            ):
                task.params.prompt = message.content
                await self._queue_service.put_task(task)
            return
        except:
            pass
        # variations?
        if message.reference is not None and message.reference.message_id is not None:
            try:
                task_id = await self._queue_service.lookup_task_by_msg(
                    message.reference.message_id
                )
                await self._queue_service.map_msg2task(message.id, task_id)
                task = await self._queue_service.get_task_by_id(task_id)
                if (
                    task.command == Command.New
                    and task.params.prompt
                    and task.params.prompt != message.content
                ):
                    task.params.prompt = message.content
                    await self._queue_service.put_task(task)
                return
            except:
                pass
        # new generation - 1st message
        t__ = [
            await self._queue_service.get_task_by_id(t)
            for t in self._current_tasks.keys()
        ]
        t_ = [
            t
            for t in t__
            if t.command == Command.New
            and isinstance(t.params, GenerateTask)
            and self.clean_str.sub(
                "", re.split(self.percent_re, t.params.prompt)[0].split("--")[0]
            )[:255]
            in self.clean_str.sub("", message.content.split("--")[0])
        ]
        if len(t_) > 1:
            t_ = sorted(t_, key=lambda i: len(i.params.prompt))
        if not t_:
            self._logger.error(f"task for {message.content} was not found")
            self._logger.info(t__)
            return
        await self._queue_service.map_msg2task(message.id, t_[0].uuid)

    async def on_message(self, message: Message):
        try:
            self._logger.debug(f"{message.id} {message.content} {message.attachments}")
            await self._ensure_task(message)
            try:
                uid = await self._queue_service.lookup_task_by_msg(message.id)
            except:
                try:
                    if (
                        message.reference is not None
                        and message.reference.message_id is not None
                    ):

                        uid = await self._queue_service.lookup_task_by_msg(
                            message.reference.message_id
                        )
                    else:
                        cnt.REQ_BY_METHOD_ERROR.labels(
                            self._human_name, "unknown", "TaskNotFound"
                        ).inc()
                        self._logger.error(f"uid for {message.content} was not found")
                        return
                except:
                    cnt.REQ_BY_METHOD_ERROR.labels(
                        self._human_name, "unknown", "TaskNotFound"
                    ).inc()
                    self._logger.error(f"uid for {message.content} was not found")
                    return

            if "Waiting to start" in message.content:
                if "test_123" in message.content:
                    self._logger.debug(f"{uid} {message}")
                task = await self._queue_service.get_task_by_id(uid)
                task.status = Outcome.Pending
                task.progress = 0
                task.discord_msg_id = message.id
                await self._queue_service.put_task(task)
            if "Open on website" in message.content or len(message.attachments):
                if "test_123" in message.content:
                    self._logger.debug(f"{uid} {message}")
                task = await self._queue_service.get_task_by_id(uid)
                task.status = Outcome.Success
                task.progress = 100
                task.deliverable = TaskDeliverable(
                    url=message.attachments[0].url,
                    filename=message.attachments[0].filename,
                )
                task.discord_msg_id = message.id
                self._logger.debug(message.attachments[0].url)
                await self._queue_service.put_task(task)
                del self._current_tasks[uid]
                cnt.SUCCEED.labels(self._human_name).inc()
                self._logger.info(len(self._current_tasks))
            self._eviction_count = 0
            if not self._high_priority:
                self.max_task_age = timedelta(minutes=13)
        except Exception as ex:
            self._logger.error(ex)

    async def on_message_edit(self, _, after: Message):
        self._logger.debug(f"{after.id} {after.content}")
        await self._ensure_task(after)
        if after.content.endswith("(Stopped)"):
            try:
                uid = await self._queue_service.lookup_task_by_msg(after.id)
            except:
                try:
                    if (
                        after.reference is not None
                        and after.reference.message_id is not None
                    ):

                        uid = await self._queue_service.lookup_task_by_msg(
                            after.reference.message_id
                        )
                    else:
                        return
                except:
                    self._logger.error(f"uid for {after.content} was not found")
                    return
            task = await self._queue_service.get_task_by_id(uid)
            task.status = Outcome.Failure
            await self._queue_service.put_task(task)
            del self._current_tasks[uid]
            self._logger.info(len(self._current_tasks))
        if "%" in after.content:
            try:
                uid = await self._queue_service.lookup_task_by_msg(after.id)
            except:
                try:
                    if (
                        after.reference is not None
                        and after.reference.message_id is not None
                    ):

                        uid = await self._queue_service.lookup_task_by_msg(
                            after.reference.message_id
                        )
                    else:
                        return
                except:
                    self._logger.error(f"uid for {after.content} was not found")
                    return
            task = await self._queue_service.get_task_by_id(uid)
            if mo := self.progress_str.search(after.content):
                task.progress = int(mo.group(1))
                await self._queue_service.put_task(task)
            if mo := self.mode_str.search(after.content):
                if self._high_priority and mo.group(1) != "fast":
                    cnt.REQ_BY_METHOD_ERROR.labels(
                        self._human_name, "unknown", "NotInFastMode"
                    ).inc()
                    self._logger.error(f"not in fast mode {after.content}")

    async def set_fast_mode(self):
        self._logger.info(f"bot id {self._bot_id} settings Fast mode...")

    async def _send_req(self, payload: dict) -> str:
        header = {"authorization": self._user_access_token}
        async with aiohttp.ClientSession(headers=header) as session:
            kwargs = {}
            if self._proxy:
                kwargs["proxy"] = "http://" + self._proxy
            async with session.post(
                self.url,
                json=payload,
                **kwargs,
            ) as response:
                if response.status > 299:
                    self._logger.error(f"{self._bot_id}: {response}")
                    text = await response.text()
                    txt = f"Unexpected response {response.status} {text}"

                    if response.status == 400:
                        raise BadRequest(txt)
                    elif response.status == 429:
                        raise TooManyRequest(txt)
                    elif response.status == 401:
                        raise NotAuthorized(txt)
                    raise GenericRequestError(txt)
                return await response.text()

    async def send_prompt(self, prompt: str):
        options = [{"type": 3, "name": "prompt", "value": prompt}]
        payload = {
            "type": 2,
            "application_id": "936929561302675456",
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "session_id": "2fb980f65e5c9a77c96ca01f2c242cf6",
            "data": {
                "version": "1077969938624553050",
                "id": "938956540159881230",
                "name": "imagine",
                "type": 1,
                "options": options,
                "application_command": {
                    "id": "938956540159881230",
                    "application_id": "936929561302675456",
                    "version": "1077969938624553050",
                    "default_permission": True,
                    "default_member_permissions": None,
                    "type": 1,
                    "nsfw": False,
                    "name": "imagine",
                    "description": "Create images with Midjourney",
                    "dm_permission": True,
                    "options": [
                        {
                            "type": 3,
                            "name": "prompt",
                            "description": "The prompt to imagine",
                            "required": True,
                        }
                    ],
                },
                "attachments": [],
            },
        }
        return await self._send_req(payload)

    async def request_variations(
        self, img_idx: int, dscrd_msg_id: int, dscrd_img_nm: str
    ):
        f_name = dscrd_img_nm.split("_")[-1].split(".")[0]
        payload = {
            "type": 3,
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "message_flags": 0,
            "message_id": dscrd_msg_id,
            "application_id": "936929561302675456",
            "session_id": "1f3dbdf09efdf93d81a3a6420882c92c",
            "data": {
                "component_type": 2,
                "custom_id": "MJ::JOB::variation::{}::{}".format(img_idx, f_name),
            },
        }
        return await self._send_req(payload)

    async def request_upscale(self, img_idx: int, dscrd_msg_id: int, dscrd_img_nm: str):
        raise NotImplementedError("Must be cropped from original image")
