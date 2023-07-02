import abc
from typing import Optional
from uuid import UUID

from ..domain.task import RouteLabel, Task
from ..foundation import Priority


class IQueueService(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def get_next_task_id(self, route_label: RouteLabel) -> Optional[UUID]:
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
    async def publish_task(self, uid: UUID, route_label: RouteLabel):
        pass

    @abc.abstractmethod
    async def push_back_task_id(self, task_id: str, route_label: RouteLabel):
        pass

    @abc.abstractmethod
    async def map_msg2task(self, msg_id: int, task_id: UUID):
        pass

    @abc.abstractmethod
    async def lookup_task_by_msg(self, msg_id: int) -> UUID:
        pass

    @abc.abstractmethod
    async def get_queue_len(self, route_label: RouteLabel) -> int:
        pass

    @abc.abstractmethod
    async def put_ticket(self, route_label: RouteLabel):
        pass

    @abc.abstractmethod
    async def count_tickets(self, route_label: RouteLabel) -> int:
        pass
