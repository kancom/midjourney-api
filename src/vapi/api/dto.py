import uuid as uuid_pkg
from typing import Optional, Union

from pydantic import BaseModel, Field
from vapi.application import (Command, GenerateTask, ImagePosition, Outcome,
                              Priority, TaskDeliverable, VariationTask)


class RequestNew(BaseModel):
    prompt: str
    priority: Priority = Priority.Low
    uuid: Optional[uuid_pkg.UUID] = Field(default_factory=uuid_pkg.uuid4)


class RequestVariation(BaseModel):
    priority: Priority = Priority.Low
    position: ImagePosition
    uuid: uuid_pkg.UUID


class ResponseStatus(BaseModel):
    priority: Priority = Priority.Low
    command: Command
    params: Union[GenerateTask, VariationTask]
    status: Outcome = Outcome.New
    progress: Optional[int] = None
    deliverable: Optional[TaskDeliverable] = None
