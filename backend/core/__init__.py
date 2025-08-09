# backend/core/__init__.py
from .config import Settings, settings
from .http_client import get_client, set_client

__all__ = [
    "Settings",
    "settings",
    "get_client",
    "set_client",
]
