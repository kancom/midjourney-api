from dependency_injector import containers, providers

from vapi.infrastructure import RedisQueueRepo
from vapi.infrastructure.redis_base import init_redis_pool
from vapi.settings import Settings


class Container(containers.DeclarativeContainer):
    wiring_config = containers.WiringConfiguration(packages=["vapi.api"])

    settings = providers.Configuration(pydantic_settings=[Settings()])
    redis_conn = providers.Resource(init_redis_pool, settings.redis_dsn)

    queue_service = providers.Singleton(RedisQueueRepo, redis=redis_conn)
