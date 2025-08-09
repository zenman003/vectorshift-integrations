from typing import Optional

import httpx

_client: Optional[httpx.AsyncClient] = None


def set_client(client: httpx.AsyncClient) -> None:
    global _client
    _client = client


def get_client() -> Optional[httpx.AsyncClient]:
    return _client


