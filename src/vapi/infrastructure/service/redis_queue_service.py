import json
from datetime import timedelta
from typing import Optional
from uuid import UUID

import vapi.infrastructure.counters as cnt
from vapi.application import IQueueService, NotInCollection, RouteLabel, Task

from ..redis_base import RedisVolatileRepo


class RedisQueueRepo(RedisVolatileRepo, IQueueService):
    task_queue = "queue"
    ttl = timedelta(hours=24)

    @classmethod
    def _get_q_name_by_prior(cls, route_label: RouteLabel):
        result = f"{cls.task_queue}_{route_label.bot_pool}_{route_label.priority.value}"
        if route_label.bot_id:
            result = f"{result}_{route_label.bot_id}"
        return result

    async def push_back_task_id(self, task_id: str, route_label: RouteLabel):
        q_nm = self._get_q_name_by_prior(route_label)
        await self._redis.lpush(q_nm, task_id)

    async def get_task_by_id(self, uid: UUID) -> Task:
        c = await self._redis.get(str(uid))
        if c is None:
            raise NotInCollection(f"{uid} was not found")
        return Task(**json.loads(c))

    async def get_next_task_id(self, route_label: RouteLabel) -> Optional[UUID]:
        q_nm = self._get_q_name_by_prior(route_label)
        c = await self._redis.rpop(q_nm)
        if c is not None:
            return UUID(c)

    async def put_task(self, task: Task):
        await self._redis.set(
            str(task.uuid), value=task.json(), ex=int(self.ttl.total_seconds())
        )

    async def publish_task(self, uid: UUID, route_label: RouteLabel):
        q_nm = self._get_q_name_by_prior(route_label)
        await self._redis.lpush(q_nm, str(uid))
        length = await self._redis.llen(q_nm)
        cnt.INC_QUEUE_LEN.labels(q_nm).set(length)

    async def del_task_by_id(self, uid: UUID):
        await self._redis.delete(str(uid))

    async def map_msg2task(self, msg_id: int, task_id: UUID):
        await self._redis.set(
            msg_id, value=str(task_id), ex=int(self.ttl.total_seconds())
        )

    async def lookup_task_by_msg(self, msg_id: int) -> UUID:
        c = await self._redis.get(msg_id)
        if c is None:
            raise NotInCollection(f"{msg_id} was not found")
        return UUID(c)

    async def get_queue_len(self, route_label: RouteLabel) -> int:
        q_nm = self._get_q_name_by_prior(route_label)
        return await self._redis.llen(q_nm)

    async def count_tickets(self, route_label: RouteLabel) -> int:
        q_nm = self._get_q_name_by_prior(route_label)
        return len(await self._redis.keys(f"ticket_{q_nm}*"))

    async def put_ticket(self, route_label: RouteLabel):
        if route_label.bot_id is None:
            raise ValueError("bot_id is empty")
        key = self._get_q_name_by_prior(route_label)
        await self._redis.set(
            f"ticket_{key}", 1, ex=int(timedelta(minutes=5).total_seconds())
        )
