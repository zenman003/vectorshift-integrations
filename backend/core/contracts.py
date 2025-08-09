from __future__ import annotations

from typing import Optional, Protocol


class KeyValueStore(Protocol):
    async def set(self, key: str, value: str, expire: Optional[int] = None) -> None: ...

    async def get(self, key: str) -> Optional[bytes]: ...

    async def delete(self, key: str) -> None: ...
