import redis.asyncio as redis
from redis import Redis


class RedisVolatileRepo:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis


async def init_redis_pool(redis_dsn: str):
    redis_conn = redis.from_url(redis_dsn, encoding="utf-8", decode_responses=True)
    yield redis_conn
