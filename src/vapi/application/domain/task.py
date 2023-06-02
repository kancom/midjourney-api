import uuid as uuid_pkg
from typing import Optional, Union

from pydantic import BaseModel
from pydantic.fields import Field

from ..foundation import Command, ImagePosition, Outcome, Priority


class GenerateTask(BaseModel):
    prompt: str


class VariationTask(BaseModel):
    position: ImagePosition


class TaskDeliverable(BaseModel):
    url: Optional[str] = None
    filename: str


class RouteLabel(BaseModel):
    priority: Priority = Priority.Low
    bot_id: Optional[int] = None
    bot_pool: str


class Task(BaseModel):
    uuid: uuid_pkg.UUID = Field(default_factory=uuid_pkg.uuid4)
    route_label: RouteLabel
    command: Command
    error: Optional[str] = None
    params: Union[GenerateTask, VariationTask]
    status: Outcome = Outcome.New
    progress: Optional[int] = None
    deliverable: Optional[TaskDeliverable] = None
    discord_msg_id: Optional[int] = None
