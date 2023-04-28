import abc
from typing import Optional
from uuid import UUID

from ..domain.task import Task
from ..foundation import Priority


class IQueueService(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def get_next_task_id(
        self, queue_priority: Priority, bot_id: Optional[int] = None
    ) -> Optional[UUID]:
        pass

    @abc.abstractmethod
    async def get_task_by_id(self, uid: UUID) -> Task:
        pass

    @abc.abstractmethod
    async def del_task_by_id(self, uid: UUID):
        pass

    @abc.abstractmethod
    async def put_task(self, task: Task):
        pass

    @abc.abstractmethod
    async def publish_task(
        self, uid: UUID, queue_priority: Priority, bot_id: Optional[int] = None
    ):
        pass

    @abc.abstractmethod
    async def push_back_task_id(self, task_id: str, queue_priority: Priority):
        pass

    @abc.abstractmethod
    async def map_msg2task(self, msg_id: int, task_id: UUID):
        pass

    @abc.abstractmethod
    async def lookup_task_by_msg(self, msg_id: int) -> UUID:
        pass
