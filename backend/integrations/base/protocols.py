from __future__ import annotations

from typing import Protocol, List, Any
from fastapi import Request

from integrations.integration_item import IntegrationItem


class IntegrationAdapter(Protocol):
    async def authorize(self, user_id: str, org_id: str) -> str:
        """Return an authorization URL or token exchange URL."""
        ...

    async def oauth_callback(self, request: Request) -> Any:
        """Handle provider OAuth callback; return a response (e.g., HTMLResponse)."""
        ...

    async def get_credentials(self, user_id: str, org_id: str) -> Any:
        """Retrieve and return provider credentials from storage."""
        ...

    async def list_items(self, credentials: str) -> List[IntegrationItem]:
        """Return list of normalized IntegrationItem objects for this provider."""
        ...
