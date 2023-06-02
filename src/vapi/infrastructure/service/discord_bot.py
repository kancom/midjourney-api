import asyncio
import itertools
import random
import re
import sys
from asyncio.tasks import sleep
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

import aiohttp
import discord
import vapi.infrastructure.counters as cnt
from discord import Client
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


class Mode(Enum):
    Relaxed = 1
    Fast = 2


class Bot(Client):
    class Info(BaseModel):
        mode: Mode
        queue: int
        fast_hours: int

    class BotInitCont(BaseModel):
        bot_id: int
        bot_pool: str
        human_name: str
        high_priority: bool = False
        channel_id: int
        server_id: int
        user_access_token: str
        bot_access_token: str
        proxy: Optional[str] = None
        captcha_service: Any

        def __hash__(self) -> int:
            return hash(
                tuple(getattr(self, f) for f in self.schema()["properties"].keys())
            )

    url = "https://discord.com/api/v9/interactions"
    MJ_BOT_ID = 936929561302675456
    max_proc_time: timedelta = timedelta(minutes=5)
    capacity = {True: 10, False: 1}
    progress_str = re.compile(r"\(([0-9]+)%\)")
    mode_str = re.compile(r"\(([a-z]+), ([a-z]+)\)")
    clean_str = re.compile(r"[^A-Za-z]")
    re_url = re.compile(r"http(s)?://[^\s]+")
    percent_re = r"\d+%"
    max_evictions = 5
    min_fast_hours = 15 * 60  # 20 minutes
    _info: Optional["Bot.Info"] = None

    def __init__(
        self,
        *,
        init_cont: BotInitCont,
        queue_service: IQueueService,
        **options: Any,
    ) -> None:
        super().__init__(
            # intents=Intents.all(),
            **options
        )
        self._bot_id = init_cont.bot_id
        self._bot_pool = init_cont.bot_pool
        self._human_name = init_cont.human_name
        self._high_priority = init_cont.high_priority
        self._channel_id = init_cont.channel_id
        self._server_id = init_cont.server_id
        self._user_access_token = init_cont.user_access_token
        self._bot_access_token = init_cont.bot_access_token
        self._captcha_src = init_cont.captcha_service
        self._proxy = init_cont.proxy
        if self._proxy and self._proxy.count(":") == 3:
            self._proxy = "{2}:{3}@{0}:{1}".format(*self._proxy.split(":"))

        self._queue_service = queue_service
        self._current_tasks: Dict[UUID, datetime] = {}
        self._logger = logger.bind(human=self._human_name)
        self._logger.debug(self._proxy)
        self._eviction_count = 0
        self._sleeping = False
        self.max_task_age = (
            timedelta(minutes=10) if self._high_priority else timedelta(minutes=13)
        )

    @property
    def identity(self) -> str:
        return self._human_name or str(self._bot_id)

    async def _worker(self):
        self._logger.info(f"Bot id {self._bot_id} worker starting")
        tasks_processed = 0
        await asyncio.sleep(random.randrange(30))
        while self.status != discord.enums.Status.online:
            await asyncio.sleep(5)

        await self.send_info_cmd()
        while True:
            try:
                task_id = None
                await asyncio.sleep(1)
                while self._sleeping:
                    await asyncio.sleep(60)

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
                        cnt.REQ_ERROR.labels(
                            self._human_name, "generic", "TaskEviction"
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
                if tasks_processed > 0 and tasks_processed % 10 == 0:
                    await self.send_info_cmd()
                    tasks_processed = 0
                for _ in range(5):
                    if self._info is not None:
                        break
                    await asyncio.sleep(1)
                for p, bot in itertools.product(Priority, (self._bot_id, None)):
                    if (
                        self._high_priority
                        and p in (Priority.Low, Priority.Normal)
                        # ) or (
                        #     not self._high_priority and p in (Priority.VIP, Priority.High)
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
                    cnt.BOT_STATE.labels(self._human_name).set(int(self._high_priority))
                    tasks_processed += 1
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
                    cnt.REQ_ERROR.labels(
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
                cnt.REQ_ERROR.labels(self._human_name, "generic", str(type(ex))).inc()
        await self.close()

    async def start(self):
        t = None
        try:
            t = asyncio.create_task(self._worker())
            self._logger.info(f"Bot id {self._bot_id} discord coroutine starting")
            # if self._high_priority:
            #     await self.set_fast_mode()
            await super().start(self._user_access_token)
        except asyncio.exceptions.CancelledError:
            if t is not None:
                t.cancel()
            self._logger.warning("cancelled")
            return
        except Exception as ex:
            self._logger.error(ex)

    def str_in_str(self, substr: str, string: str) -> bool:
        return self.clean_str.sub(
            "",
            re.split(self.percent_re, self.re_url.sub("", substr))[0].split("--")[0],
        ) in self.clean_str.sub(
            "",
            re.split(self.percent_re, self.re_url.sub("", string))[0].split("--")[0],
        )

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
            and (
                self.str_in_str(t.params.prompt, message.content)
                or (
                    len(message.embeds) > 0
                    and message.embeds[0].footer.text
                    and self.str_in_str(t.params.prompt, message.embeds[0].footer.text)
                )
            )
        ]
        if len(t_) > 1:
            t_ = sorted(t_, key=lambda i: len(i.params.prompt))
        if not t_:
            self._logger.error(f"task for {message.content} was not found")
            self._logger.info(t__)
            return
        await self._queue_service.map_msg2task(message.id, t_[0].uuid)

    async def on_message(self, message: Message):
        if message.channel.id != self._channel_id:
            return

        for emb in message.embeds:
            self._logger.debug(f"{emb.title}: {emb.description}")
            self._logger.debug(f"{emb.image}")
            self._logger.debug(f" {emb.to_dict()}")
            if not await self._dispatch_embed(emb, message):
                return
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
                        cnt.REQ_ERROR.labels(
                            self._human_name, "generic", "TaskNotFound"
                        ).inc()
                        self._logger.error(f"uid for {message.content} was not found")
                        return
                except:
                    cnt.REQ_ERROR.labels(
                        self._human_name, "generic", "TaskNotFound"
                    ).inc()
                    self._logger.error(f"uid for {message.content} was not found")
                    return

            if "Waiting to start" in message.content:
                task = await self._queue_service.get_task_by_id(uid)
                task.status = Outcome.Pending
                task.progress = 0
                task.discord_msg_id = message.id
                await self._queue_service.put_task(task)
            if "Open on website" in message.content or len(message.attachments):
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

    @classmethod
    def _parse_info(cls, d: dict) -> Info:
        mode = Mode.Relaxed if "Relaxed" in d["Job Mode"] else Mode.Fast
        pfx = "fast" if mode == Mode.Fast else "relax"
        queue = int(d.get(f"Queued Jobs ({pfx})", 0))

        return cls.Info(
            mode=mode,
            queue=queue,
            fast_hours=3600 * float(d["Fast Time Remaining"].split("/")[0]),
        )

    async def _send_tg_notification(self, text):
        token = "6085315593:AAHLqx3KiscXuRqlun0pPmfqvZbtuvW2UPE"
        chat_id = -1001850006791
        text = f"{self._human_name} {text}!"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status > 299:
                    self._logger.error("failed to notify")

    async def _dispatch_embed(self, embed: discord.Embed, msg: Message) -> bool:
        if embed.description:
            if "human" in embed.description:
                for _ in range(2):
                    try:
                        labels = {
                            c.label: c.custom_id for c in msg.components[0].children
                        }
                        label = await self._captcha_src.solve(
                            embed.image.url, list(labels.keys())
                        )
                        await self._press_btn(msg.id, labels[label], flags=64)
                        break
                    except aiohttp.ClientConnectorError:
                        continue
                    except Exception as ex:
                        self._logger.debug(embed.image)
                        self._logger.debug(embed.image.url)
                        self._logger.debug(msg.components)
                        self._logger.exception(ex)

            elif (
                "third-party" in embed.description
                and "acknowledge" in embed.description
            ):
                labels = {
                    c.label: c.custom_id
                    for c in msg.components[0].children
                    if "Ack" in c.label
                }
                if labels:
                    await self._press_btn(msg.id, list(labels.values())[0], flags=64)
                else:
                    self._logger.error(f"can't find Ack {msg.components[0].children}")
            elif "blocked" in embed.description and " ban " in embed.description:
                msg_ = f"I was banned {embed.description}. Sleep for 25 hours"
                self._logger.error(msg_)
                s_ = embed.description.split(":")
                i_ = 25 * 60 * 60
                if len(s_) > 1:
                    until = datetime.fromtimestamp(int(2))
                    i_ = (datetime.utcnow() - until).total_seconds()
                await self._send_tg_notification(msg_)
                self._sleeping = True
                await asyncio.sleep(i_)
                self._sleeping = False
                return False
            elif "billing" in embed.description:
                msg_ = embed.description
                self._logger.error(msg_)
                await self._send_tg_notification(msg_)
                return False

        await self._ensure_task(msg)
        try:
            uid = await self._queue_service.lookup_task_by_msg(msg.id)
        except:
            try:
                if msg.reference is not None and msg.reference.message_id is not None:

                    uid = await self._queue_service.lookup_task_by_msg(
                        msg.reference.message_id
                    )
                else:
                    return False
            except:
                self._logger.error(f"uid for {msg.content} was not found")
                return False
            task = await self._queue_service.get_task_by_id(uid)
            task.status = Outcome.Failure
            task.progress = 0
            task.error = embed.description
            await self._queue_service.put_task(task)
            del self._current_tasks[uid]
            self._logger.info(len(self._current_tasks))
            return False

        return True

    async def on_message_edit(self, _, after: Message):
        if after.channel.id != self._channel_id:
            return
        self._logger.debug(
            f"{after.id}, {after.channel.id}, {after.created_at}, {after.content}, {after.embeds}"
        )
        for emb in after.embeds:
            if emb.description is not None and "Subscription" in emb.description:
                try:
                    lines = emb.description.split("\n")
                    d = {
                        l.split(":")[0]
                        .strip()
                        .replace("*", ""): l.split(":")[1]
                        .strip()
                        for l in lines
                        if ":" in l
                    }
                    self._info = self._parse_info(d)
                    if (
                        self._high_priority
                        and self._info.fast_hours < self.min_fast_hours
                    ):
                        self._logger.info(
                            f"No more Fast remained: {self._info.fast_hours}. Switching to relaxed"
                        )
                        await asyncio.sleep(10)
                        self._high_priority = False
                        await self.send_setrelaxed_cmd()
                        cnt.BOT_STATE.labels(self._human_name).set(
                            int(self._high_priority)
                        )
                    elif (
                        not self._high_priority
                        and self._info.fast_hours > 1.5 * self.min_fast_hours
                    ):
                        await asyncio.sleep(10)
                        self._high_priority = True
                        await self.send_setfast_cmd()
                        cnt.BOT_STATE.labels(self._human_name).set(
                            int(self._high_priority)
                        )
                    self._logger.debug(self._info)
                    cnt.FAST_TIME_REMAINING.labels(self._human_name).set(
                        self._info.fast_hours
                    )
                except Exception as ex:
                    self._logger.exception(ex)
            else:
                self._logger.debug(
                    f"{emb.title}: {emb.description}, {emb.image}. {emb.fields}, {emb.footer}"
                )
                if not await self._dispatch_embed(emb, after):
                    return

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
                    cnt.REQ_ERROR.labels(
                        self._human_name, "generic", "NotInFastMode"
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
        custom_id = "MJ::JOB::variation::{}::{}".format(img_idx, f_name)
        return await self._press_btn(dscrd_msg_id, custom_id)

    async def _press_btn(self, dscrd_msg_id: int, custom_id: str, flags: int = 0):
        payload = {
            "type": 3,
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "message_flags": flags,
            "message_id": dscrd_msg_id,
            "application_id": "936929561302675456",
            "session_id": "1f3dbdf09efdf93d81a3a6420882c92c",
            "data": {
                "component_type": 2,
                "custom_id": custom_id,
            },
        }
        return await self._send_req(payload)

    async def send_info_cmd(self):
        payload = {
            "type": 2,
            "application_id": "936929561302675456",
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "session_id": "2fb980f65e5c9a77c96ca01f2c242cf6",
            "data": {
                "version": "987795925764280356",
                "id": "972289487818334209",
                "name": "info",
                "type": 1,
                "options": [],
                "application_command": {
                    "id": "972289487818334209",
                    "application_id": "936929561302675456",
                    "version": "987795925764280356",
                    "default_member_permissions": None,
                    "type": 1,
                    "nsfw": False,
                    "name": "info",
                    "description": "View information about your profile.",
                    "dm_permission": True,
                    "contexts": None,
                },
                "attachments": [],
            },
        }
        self._logger.debug("request /INFO")
        return await self._send_req(payload)

    async def send_setfast_cmd(self):
        payload = {
            "type": 2,
            "application_id": "936929561302675456",
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "session_id": "adbb78aa583b20f4e58f2ef23ce89774",
            "data": {
                "version": "987795926183731231",
                "id": "972289487818334212",
                "name": "fast",
                "type": 1,
                "options": [],
                "application_command": {
                    "id": "972289487818334212",
                    "application_id": "936929561302675456",
                    "version": "987795926183731231",
                    "default_member_permissions": None,
                    "type": 1,
                    "nsfw": False,
                    "name": "fast",
                    "description": "Switch to fast mode",
                    "dm_permission": False,
                },
                "attachments": [],
            },
        }
        return await self._send_req(payload)

    async def send_setrelaxed_cmd(self):
        payload = {
            "type": 2,
            "application_id": "936929561302675456",
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "session_id": "adbb78aa583b20f4e58f2ef23ce89774",
            "data": {
                "version": "987795926183731232",
                "id": "972289487818334213",
                "name": "relax",
                "type": 1,
                "options": [],
                "application_command": {
                    "id": "972289487818334213",
                    "application_id": "936929561302675456",
                    "version": "987795926183731232",
                    "default_member_permissions": None,
                    "type": 1,
                    "nsfw": False,
                    "name": "relax",
                    "description": "Switch to relax mode",
                    "dm_permission": False,
                },
                "attachments": [],
            },
        }
        return await self._send_req(payload)
