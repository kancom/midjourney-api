import asyncio
import itertools
import random
import re
import sys
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
from vapi.application import (Command, IQueueService, NotInCollection, Outcome,
                              Priority, RouteLabel, TaskDeliverable)
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
    Offline = 0
    Relaxed = 1
    Fast = 2


class DispatchOutcome(Enum):
    Abort = 1
    Retry = 2
    Continue = 3


class Bot(Client):
    class Info(BaseModel):
        mode: Mode
        queue: int
        fast_hours: int
        active: bool

    class BotInitCont(BaseModel):
        bot_id: int
        bot_pool: str
        human_name: str
        high_priority: bool = False
        channel_id: int
        server_id: int
        user_access_token: str
        proxy: Optional[str] = None
        captcha_service: Any

        def __hash__(self) -> int:
            return hash(
                tuple(getattr(self, f) for f in self.schema()["properties"].keys())
            )

    url = "https://discord.com/api/v9/interactions"
    MJ_BOT_ID = 936929561302675456
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
        self._captcha_src = init_cont.captcha_service
        self._proxy = init_cont.proxy
        if self._proxy and self._proxy.count(":") == 3:
            self._proxy = "{2}:{3}@{0}:{1}".format(*self._proxy.split(":"))

        self._queue_service = queue_service
        self._current_tasks: Dict[UUID, datetime] = {}
        self._logger = logger.bind(human=self._human_name)
        self._logger.debug(self._proxy)
        self._eviction_count = 0
        self._offline = False
        self.max_task_age = (
            timedelta(minutes=10) if self._high_priority else timedelta(minutes=13)
        )
        self._loop = None

    @property
    def identity(self) -> str:
        return self._human_name or str(self._bot_id)

    async def _assist_2peers(self):
        if (
            self._info is None
            or self._info.fast_hours == 0
            and len(self._current_tasks) > 0
        ):
            return
        rl = RouteLabel(priority=Priority.High, bot_pool=self._bot_pool)
        high_len = await self._queue_service.get_queue_len(rl)
        high_tickets = await self._queue_service.count_tickets(rl)
        rl = RouteLabel(priority=Priority.Normal, bot_pool=self._bot_pool)
        normal_len = await self._queue_service.get_queue_len(rl)
        normal_tickets = await self._queue_service.count_tickets(rl)
        if self._high_priority:
            # if need to help Normal queue
            if (
                normal_len > normal_tickets  # peer's len's too long
                and high_tickets > high_len  # our capacity is enough
                and high_tickets > 1  # one is spare (1 is self already)
            ):
                self._logger.info("switching to assist to Normal queue")
                self._high_priority = False
                await self.send_setrelaxed_cmd()
        else:
            # if need to help High queue
            if (
                high_len > high_tickets
                and normal_tickets > normal_len
                and self._info.fast_hours > self.min_fast_hours
                # and normal_tickets > 0  # one is spare
            ):
                self._logger.info("switching to assist to High queue")
                self._high_priority = True
                await self.send_setfast_cmd()

    async def _recheck_info(self):
        if not self._offline:
            await self.send_info_cmd()
        await asyncio.sleep(10 * 60)
        if self._loop is None:
            raise ValueError("loop is None")
        self._loop.create_task(self._recheck_info())

    async def _worker(self):
        self._logger.info(f"Bot id {self._bot_id} worker starting")
        tasks_processed = 0

        cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(Mode.Offline.value)
        await asyncio.sleep(random.randrange(30))
        while self.status != discord.enums.Status.online:
            await asyncio.sleep(5)

        await self.send_info_cmd()
        self._loop = asyncio.get_running_loop()
        self._loop.create_task(self._recheck_info())
        while True:
            try:
                task_id = None
                await asyncio.sleep(1)
                while self._offline:
                    await asyncio.sleep(60)

                # stuck task cleanup
                now = datetime.utcnow()
                ev = [
                    k
                    for k, v in self._current_tasks.items()
                    if now - v > self.max_task_age
                ]
                if len(ev):
                    # and not self._high_priority:
                    self.max_task_age = timedelta(minutes=15)
                    self._eviction_count += 1
                    self._logger.warning(ev)
                for t in ev:
                    cnt.REQ_ERROR.labels(
                        self._human_name, "generic", "TaskEviction"
                    ).inc()
                    try:
                        t = await self._queue_service.get_task_by_id(t)
                    except NotInCollection:
                        # already expired
                        break
                    t.status = Outcome.Failure
                    await self._queue_service.put_task(t)
                self._current_tasks = {
                    k: v
                    for k, v in self._current_tasks.items()
                    if now - v < self.max_task_age
                }
                if self._eviction_count > self.max_evictions:
                    self._logger.critical(
                        f"Exit. Too many evictions {self._eviction_count}"
                    )
                    break
                # clean done

                # assistance to peer
                await self._assist_2peers()
                await self._queue_service.put_ticket(
                    RouteLabel(
                        priority=Priority.High
                        if self._high_priority
                        else Priority.Normal,
                        bot_pool=self._bot_pool,
                        bot_id=self._bot_id,
                    )
                )

                if len(self._current_tasks) >= self.capacity[self._high_priority]:
                    continue

                cnt.QUEUE_LEN.labels(self._human_name, self._high_priority).set(
                    len(self._current_tasks)
                )
                for _ in range(5):
                    if self._info is not None:
                        break
                    await asyncio.sleep(1)

                if self._info and self._info.queue > 0:
                    continue

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
                        self._logger.debug(f"{task_id} from {route_label}")
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
                        task.route_label.bot_id = None
                        task.status = Outcome.New
                        await self._queue_service.put_task(task)
                        await self._queue_service.push_back_task_id(
                            str(task.uuid), task.route_label
                        )
                    if task_id is not None and task_id in self._current_tasks:
                        del self._current_tasks[task_id]
                    self._logger.debug(self._current_tasks)
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
            cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(
                Mode.Offline.value
            )
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
                and len(message.content)
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
                    and len(message.content)
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

        try:
            self._logger.debug(f"{message.id},{message.content}, {message.attachments}")
            # if there are some tasks @ mj bot's queue
            if self._info and self._info.queue > 0 and len(message.attachments):
                # this is a foreign task
                self._info.queue -= 1

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
            for emb in message.embeds:
                self._logger.debug(f"{emb.title}: {emb.description}")
                self._logger.debug(f"{emb.image}")
                self._logger.debug(f" {emb.to_dict()}")
                outc = await self._dispatch_embed(emb, message)
                if outc == DispatchOutcome.Retry:
                    # if mj says there's some permanent failure e.g. subscription's expired.
                    # stop processing it here and push back to process in other workers
                    task = await self._queue_service.get_task_by_id(uid)
                    if task.command == Command.New:
                        self._logger.debug(f"push back {uid}")
                        task.route_label.bot_id = None
                        task.status = Outcome.New
                        await self._queue_service.put_task(task)
                        await self._queue_service.push_back_task_id(
                            str(task.uuid), task.route_label
                        )
                    if uid is not None and uid in self._current_tasks:
                        del self._current_tasks[uid]
                    await asyncio.sleep(10)
                    return
                elif outc == DispatchOutcome.Abort:
                    return

            if "Waiting to start" in message.content:
                task = await self._queue_service.get_task_by_id(uid)
                task.status = Outcome.Pending
                task.progress = 0
                task.discord_msg_id = message.id
                await self._queue_service.put_task(task)
            if "Open on website" in message.content or len(message.attachments):
                cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(
                    Mode.Fast.value if self._high_priority else Mode.Relaxed.value
                )
                task = await self._queue_service.get_task_by_id(uid)
                task.status = Outcome.Success
                task.progress = 100
                task.deliverable = TaskDeliverable(
                    url=message.attachments[0].url,
                    filename=message.attachments[0].filename,
                )
                task.discord_msg_id = message.id
                self._logger.debug(f"{uid}, {message.attachments[0].url}")
                await self._queue_service.put_task(task)
                if uid in self._current_tasks:
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
        mode = Mode.Relaxed
        if "Job Mode" in d and "Relaxed" not in d["Job Mode"]:
            mode = Mode.Fast
        pfx = "fast" if mode == Mode.Fast else "relax"
        queue = int(d.get(f"Queued Jobs ({pfx})", 0))
        try:
            fast_hours = float(d["Fast Time Remaining"].split("/")[0])
        except:
            fast_hours = 0
        if fast_hours == 0:
            mode = Mode.Relaxed
        return cls.Info(
            mode=mode,
            queue=queue,
            fast_hours=3600 * fast_hours,
            active="Paused" not in d["Subscription"]
            and "Inactive" not in d["Subscription"],
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

    async def _delayed_awake(self, delay: float):
        await asyncio.sleep(delay)
        self._offline = False

    async def _dispatch_embed(
        self, embed: discord.Embed, msg: Message
    ) -> DispatchOutcome:
        """dispatch and tell if needs retry at another worker"""
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
                return DispatchOutcome.Continue

            elif (
                "Your job queue is full" in embed.description
                or "concurrent job" in embed.description
            ):
                if self._info:
                    self._info.queue += 1
                return DispatchOutcome.Retry
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
                return DispatchOutcome.Retry
            elif "blocked" in embed.description and " ban " in embed.description:
                self._offline = True
                i_ = timedelta(seconds=25 * 60 * 60)
                s_ = embed.description.split(":")
                if len(s_) > 1:
                    until = datetime.fromtimestamp(int(s_[1]))
                    i_ = until - datetime.utcnow()
                msg_ = f"I was banned {embed.description}. Sleep for {i_}"
                self._logger.error(msg_)
                await self._send_tg_notification(msg_)
                cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(
                    Mode.Offline.value
                )
                if self._loop:
                    self._loop.create_task(self._delayed_awake(i_.total_seconds()))
                return DispatchOutcome.Retry
            elif (
                "billing" in embed.description
                or "Subscription is paused" in embed.description
                or "subscribing" in embed.description
            ):
                self._offline = True
                msg_ = embed.description
                self._logger.error(msg_)
                cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(
                    Mode.Offline.value
                )
                await self._send_tg_notification(msg_)
                return DispatchOutcome.Retry
            elif "run out of hours" in embed.description:
                if self._high_priority:
                    self._high_priority = False
                    await self.send_setrelaxed_cmd()
                else:
                    self._offline = True
                    msg_ = embed.description
                    self._logger.error(msg_)
                    cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(
                        Mode.Offline.value
                    )
                    await self._send_tg_notification(msg_)

                return DispatchOutcome.Retry

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
                    return DispatchOutcome.Abort
            except:
                self._logger.error(f"uid for {msg.content} was not found")
                return DispatchOutcome.Abort
        task = await self._queue_service.get_task_by_id(uid)
        task.status = Outcome.Failure
        task.progress = 0
        task.error = embed.description
        self._logger.debug(task)
        await self._queue_service.put_task(task)
        del self._current_tasks[uid]
        self._logger.info(len(self._current_tasks))
        return DispatchOutcome.Abort

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
                    if not self._info.active:
                        self._offline = True
                        msg_ = f"{self._human_name} Subscription is paused. Send manual /INFO after activation"
                        self._logger.error(msg_)
                        await self._send_tg_notification(msg_)
                    else:
                        self._offline = False
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
                    elif (
                        not self._high_priority
                        and self._info.fast_hours > 1.5 * self.min_fast_hours
                    ):
                        await asyncio.sleep(10)
                        self._high_priority = True
                        await self.send_setfast_cmd()
                    state = Mode.Offline.value
                    if self._info.active:
                        state = (
                            Mode.Fast.value
                            if self._high_priority
                            else Mode.Relaxed.value
                        )

                    cnt.BOT_STATE.labels(self._human_name, self._bot_pool).set(state)
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
                outc = await self._dispatch_embed(emb, after)
                if outc == DispatchOutcome.Abort:
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

    async def _send_req(self, payload: dict) -> str:
        header = {"authorization": self._user_access_token}
        async with aiohttp.ClientSession(headers=header) as session:
            kwargs = {}
            if self._proxy:
                kwargs["proxy"] = "http://" + self._proxy
            for trial in range(3):
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
                            await asyncio.sleep(5 * trial + 1)
                            continue
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
            "session_id": self.ws.session_id,
            # "session_id": "2fb980f65e5c9a77c96ca01f2c242cf6",
            "data": {
                "version": "1118961510123847772",
                # "version": "1077969938624553050",
                "id": "938956540159881230",
                "name": "imagine",
                "type": 1,
                "options": options,
                "application_command": {
                    "id": "938956540159881230",
                    "application_id": "936929561302675456",
                    "version": "1118961510123847772",
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
        custom_id = "MJ::JOB::variation::{}::{}".format(
            img_idx, f_name.split(".")[0][-36:]
        )
        return await self._press_btn(dscrd_msg_id, custom_id)

    async def _press_btn(self, dscrd_msg_id: int, custom_id: str, flags: int = 0):
        payload = {
            "type": 3,
            "guild_id": self._server_id,
            "channel_id": self._channel_id,
            "message_flags": flags,
            "message_id": dscrd_msg_id,
            "application_id": "936929561302675456",
            "session_id": self.ws.session_id,
            # "session_id": "1f3dbdf09efdf93d81a3a6420882c92c",
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
            "session_id": self.ws.session_id,
            # "session_id": "2fb980f65e5c9a77c96ca01f2c242cf6",
            "data": {
                "version": "1118961510123847776",
                "id": "972289487818334209",
                "name": "info",
                "type": 1,
                "options": [],
                "application_command": {
                    "id": "972289487818334209",
                    "application_id": "936929561302675456",
                    "version": "1118961510123847776",
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
            "session_id": self.ws.session_id,
            # "session_id": "adbb78aa583b20f4e58f2ef23ce89774",
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
            "session_id": self.ws.session_id,
            # "session_id": "adbb78aa583b20f4e58f2ef23ce89774",
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


# https://discord.com/api/v9/channels/1108089142883135583/application-commands/search?type=1&query=i&limit=7&include_applications=false
# {
#   "applications": null,
#   "application_commands": [
#     {
#       "id": "938956540159881230",
#       "application_id": "936929561302675456",
#       "version": "1118961510123847772",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "imagine",
#       "description": "Create images with Midjourney",
#       "dm_permission": true,
#       "contexts": [
#         0,
#         1,
#         2
#       ],
#       "options": [
#         {
#           "type": 3,
#           "name": "prompt",
#           "description": "The prompt to imagine",
#           "required": true
#         }
#       ]
#     },
#     {
#       "id": "972289487818334209",
#       "application_id": "936929561302675456",
#       "version": "1118961510123847776",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "info",
#       "description": "View information about your profile.",
#       "dm_permission": true,
#       "contexts": [
#         0,
#         1,
#         2
#       ]
#     },
#     {
#       "id": "986816068012081172",
#       "application_id": "936929561302675456",
#       "version": "1087986002192252980",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "invite",
#       "description": "Get an invite link to the Midjourney Discord server",
#       "dm_permission": true,
#       "contexts": null
#     },
#     {
#       "id": "1092492867185950852",
#       "application_id": "936929561302675456",
#       "version": "1118961510123847774",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "describe",
#       "description": "Writes a prompt based on your image.",
#       "dm_permission": true,
#       "contexts": [
#         0,
#         1,
#         2
#       ],
#       "options": [
#         {
#           "type": 11,
#           "name": "image",
#           "description": "The image to describe",
#           "required": true
#         }
#       ]
#     },
#     {
#       "id": "984273800587776053",
#       "application_id": "936929561302675456",
#       "version": "1029519354955579472",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "prefer",
#       "description": "…",
#       "dm_permission": true,
#       "contexts": null,
#       "options": [
#         {
#           "type": 2,
#           "name": "option",
#           "description": "…",
#           "options": [
#             {
#               "type": 1,
#               "name": "set",
#               "description": "Set a custom option.",
#               "options": [
#                 {
#                   "type": 3,
#                   "name": "option",
#                   "description": "…",
#                   "required": true,
#                   "autocomplete": true
#                 },
#                 {
#                   "type": 3,
#                   "name": "value",
#                   "description": "…"
#                 }
#               ]
#             },
#             {
#               "type": 1,
#               "name": "list",
#               "description": "View your current custom options."
#             }
#           ]
#         },
#         {
#           "type": 1,
#           "name": "auto_dm",
#           "description": "Whether or not to automatically send job results to your DMs."
#         },
#         {
#           "type": 1,
#           "name": "suffix",
#           "description": "Suffix to automatically add to the end of every prompt. Leave empty to remove.",
#           "options": [
#             {
#               "type": 3,
#               "name": "new_value",
#               "description": "…"
#             }
#           ]
#         },
#         {
#           "type": 1,
#           "name": "remix",
#           "description": "Toggle remix mode."
#         }
#       ]
#     },
#     {
#       "id": "972289487818334210",
#       "application_id": "936929561302675456",
#       "version": "1065569343456419862",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "private",
#       "description": "Toggle stealth mode",
#       "dm_permission": true,
#       "contexts": null
#     },
#     {
#       "id": "972289487818334211",
#       "application_id": "936929561302675456",
#       "version": "987795926183731230",
#       "default_member_permissions": null,
#       "type": 1,
#       "nsfw": false,
#       "name": "public",
#       "description": "Switch to public mode",
#       "dm_permission": true,
#       "contexts": null
#     }
#   ],
#   "cursor": {
#     "previous": "WzExMTg5NjI4ODA0ODY4NDY0ODQsIDAsIDkzODk1NjU0MDE1OTg4MTIzMF0=",
#     "next": "WzExMTg5NjI4ODA0ODY4NDY0ODQsIDcsIDEwMDA4NTA3NDM0NzkyNTUwODFd",
#     "repaired": false
#   }
# }
