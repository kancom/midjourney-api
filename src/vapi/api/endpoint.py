import uuid

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, status
from vapi.application import (Command, GenerateTask, IQueueService, Outcome,
                              RouteLabel, Task, VariationTask)
from vapi.application.foundation import NotInCollection
from vapi.wiring import Container

from .dto import RequestNew, RequestVariation, ResponseStatus

router = APIRouter()


@router.post(
    "/new",
    response_model=uuid.UUID,
    status_code=status.HTTP_201_CREATED,
)
@inject
async def make_set(
    request: RequestNew,
    queue_service: IQueueService = Depends(Provide[Container.queue_service]),
):
    try:
        await queue_service.get_task_by_id(request.uuid)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this UUID is already processing",
        )
    except NotInCollection:
        pass
    route_label = RouteLabel(priority=request.priority, bot_pool=request.route_hint)
    task = Task(
        uuid=request.uuid,
        route_label=route_label,
        command=Command.New,
        status=Outcome.New,
        params=GenerateTask(prompt=request.prompt),
    )
    await queue_service.put_task(task)
    await queue_service.publish_task(task.uuid, route_label)
    return task.uuid


@router.post(
    "/variation",
    response_model=uuid.UUID,
    status_code=status.HTTP_201_CREATED,
)
@inject
async def make_variation(
    request: RequestVariation,
    queue_service: IQueueService = Depends(Provide[Container.queue_service]),
):
    task = await queue_service.get_task_by_id(request.uuid)
    task.command = Command.Variation
    task.status = Outcome.New
    task.params = VariationTask(position=request.position)
    task.progress = 0
    task.route_label.priority = request.priority
    if task.deliverable is not None:
        task.deliverable.url = None
    await queue_service.put_task(task)
    await queue_service.publish_task(task.uuid, task.route_label)
    return task.uuid


@router.get(
    "/status",
    response_model=ResponseStatus,
    status_code=status.HTTP_200_OK,
)
@inject
async def get_status(
    uuid: uuid.UUID,
    queue_service: IQueueService = Depends(Provide[Container.queue_service]),
):
    task = await queue_service.get_task_by_id(uuid)
    await queue_service.put_task(task)
    return ResponseStatus(**task.dict(exclude={"uuid", "discord_msg_id"}))
