from __future__ import annotations

from typing import Optional

from .contracts import KeyValueStore
from .redis_client import redis_client


class RedisStore(KeyValueStore):
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> None:
        await redis_client.set(key, value)
        if expire:
            await redis_client.expire(key, expire)

    async def get(self, key: str):
        return await redis_client.get(key)

    async def delete(self, key: str) -> None:
        await redis_client.delete(key)
