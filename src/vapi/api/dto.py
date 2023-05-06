import uuid as uuid_pkg
from typing import Optional, Union

from pydantic import BaseModel, Field
from vapi.application import (Command, GenerateTask, ImagePosition, Outcome,
                              Priority, TaskDeliverable, VariationTask)


class RequestBase(BaseModel):
    priority: Priority = Priority.Low
    route_hint: str


class RequestNew(RequestBase):
    prompt: str
    uuid: Optional[uuid_pkg.UUID] = Field(default_factory=uuid_pkg.uuid4)


class RequestVariation(RequestBase):
    position: ImagePosition
    uuid: uuid_pkg.UUID


class ResponseStatus(BaseModel):
    priority: Priority = Priority.Low
    command: Command
    params: Union[GenerateTask, VariationTask]
    status: Outcome = Outcome.New
    progress: Optional[int] = None
    deliverable: Optional[TaskDeliverable] = None
