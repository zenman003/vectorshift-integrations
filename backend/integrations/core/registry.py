# backend/integrations/core/registry.py
from __future__ import annotations

from typing import Dict

from integrations.base.protocols import IntegrationAdapter

_registry: Dict[str, IntegrationAdapter] = {}


def register_adapter(name: str, adapter: IntegrationAdapter) -> None:
    key = name.lower()
    _registry[key] = adapter


def get_adapter(name: str) -> IntegrationAdapter:
    key = name.lower()
    if key not in _registry:
        raise KeyError(f"No adapter registered for '{name}'")
    return _registry[key]


def list_adapters():
    return list(_registry.keys())
