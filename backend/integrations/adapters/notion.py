# notion.py
import base64
import logging
from typing import List, Optional

import httpx
from core import settings
from core.contracts import KeyValueStore
from fastapi import HTTPException, Request

from integrations.base import StandardOAuthStrategy, oauth_close_window
from integrations.core import (
    IntegrationItem,
    ItemType,
    NotionCredentials,
)

logger = logging.getLogger(__name__)


class NotionAdapter:
    def __init__(self, http: httpx.AsyncClient, kv_store: KeyValueStore):
        """Initialize NotionAdapter with OAuth and config."""
        self.authorization_url = f"https://api.notion.com/v1/oauth/authorize?client_id={settings.notion_client_id}&response_type=code&owner=user&redirect_uri={settings.notion_redirect_uri.replace(':', '%3A').replace('/', '%2F')}"
        self.encoded_client_id_secret = base64.b64encode(
            f"{settings.notion_client_id}:{settings.notion_client_secret}".encode()
        ).decode()
        self.http = http
        self.kv_store = kv_store
        self.oauth_strategy = StandardOAuthStrategy("notion", self.kv_store)

    async def authorize(self, user_id: str, org_id: str) -> str:
        """Notion OAuth strategy - standard OAuth 2.0 flow."""
        result = await self.oauth_strategy.authorize(
            user_id, org_id, settings.notion_state_expiry_seconds
        )
        return f"{self.authorization_url}&state={result['encoded_state']}"

    async def oauth_callback(self, request: Request):
        """Notion OAuth callback strategy - uses JSON content type."""
        result = await self.oauth_strategy.callback(dict(request.query_params))
        code, user_id, org_id = result["code"], result["user_id"], result["org_id"]

        response = await self.http.post(
            "https://api.notion.com/v1/oauth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.notion_redirect_uri,
            },
            headers={
                "Authorization": f"Basic {self.encoded_client_id_secret}",
                "Content-Type": "application/json",
            },
        )

        if response.status_code != 200:
            logger.error(
                f"Failed to exchange code for token: status={response.status_code}"
            )
            raise HTTPException(
                status_code=400, detail="Failed to exchange code for token."
            )

        credentials = NotionCredentials.model_validate(response.json())
        await self.kv_store.set(
            f"notion_credentials:{org_id}:{user_id}",
            credentials.model_dump_json(),
            expire=settings.notion_credentials_expiry_seconds,
        )
        return oauth_close_window()

    async def get_credentials(self, user_id: str, org_id: str):
        """Notion credentials retrieval strategy."""
        credentials = await self.kv_store.get(f"notion_credentials:{org_id}:{user_id}")
        if not credentials:
            raise HTTPException(status_code=400, detail="No credentials found.")
        credentials_data = NotionCredentials.model_validate_json(credentials)
        await self.kv_store.delete(f"notion_credentials:{org_id}:{user_id}")
        return credentials_data

    async def list_items(self, credentials: str) -> List[IntegrationItem]:
        """List Notion items as IntegrationItems."""
        try:
            credentials_data = NotionCredentials.model_validate_json(credentials)
            response = await self.http.post(
                "https://api.notion.com/v1/search",
                json={},
                headers={
                    "Authorization": f"Bearer {credentials_data.access_token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code == 200:
                results = response.json()["results"]
                internal_items: List[IntegrationItem] = []
                for result in results:
                    item_type = result.get("object", "unknown")
                    parent_id = None
                    parent_name = None

                    if result.get("parent", {}).get("type") != "workspace":
                        parent_type = result.get("parent", {}).get("type")
                        if parent_type and parent_type in result.get("parent", {}):
                            parent_id = result.get("parent", {}).get(parent_type)

                    internal_items.append(
                        self._create_integration_item_metadata_object(
                            result, item_type, parent_id, parent_name
                        )
                    )

                logger.info(f"Retrieved {len(internal_items)} integration items")
                logger.info(f"Integration items: {internal_items}")
                return internal_items
            else:
                logger.error(
                    f"Failed to fetch Notion items: status={response.status_code}"
                )
                raise HTTPException(
                    status_code=500, detail="Failed to retrieve Notion items"
                )
        except Exception as err:
            logger.error(f"Error getting Notion items: {err}")
            raise HTTPException(
                status_code=500, detail="Failed to retrieve Notion items"
            )

    def _create_integration_item_metadata_object(
        self,
        response_json: dict,
        item_type: str,
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
    ) -> IntegrationItem:
        """Create IntegrationItem metadata from Notion API response."""
        try:
            name = self._recursive_dict_search(
                response_json.get("properties", {}), "content"
            )

            if response_json.get("parent", {}).get("type") == "workspace":
                computed_parent_id = None
            else:
                computed_parent_id = parent_id

            name = (
                self._recursive_dict_search(response_json, "content")
                if name is None
                else name
            )
            name = "multi_select" if name is None else name
            name = response_json.get("object", "") + " " + str(name)
            type_map = {
                "page": ItemType.PAGES,
                "database": ItemType.DATABASES,
            }
            return IntegrationItem(
                id=response_json.get("id"),
                type=type_map.get(item_type, ItemType.UNKNOWN),
                name=name,
                creation_time=response_json.get("created_time"),
                last_modified_time=response_json.get("last_edited_time"),
                parent_id=computed_parent_id,
                parent_path_or_name=parent_name,
            )
        except Exception as err:
            logger.error(f"Error creating integration item metadata: {err}")
        type_map = {
            "page": ItemType.PAGES,
            "database": ItemType.DATABASES,
        }
        return IntegrationItem(
            id=response_json.get("id", "unknown"),
            type=type_map.get(item_type, ItemType.UNKNOWN),
            name=f"Error parsing {response_json.get('object', 'item')}",
        )

    def _recursive_dict_search(self, data: dict, target_key: str) -> Optional[str]:
        """Recursively search for a key in a nested dict and return its value."""
        if not isinstance(data, dict):
            return None

        if target_key in data:
            return data[target_key]

        for value in data.values():
            if isinstance(value, dict):
                result = self._recursive_dict_search(value, target_key)
                if result is not None:
                    return result
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        result = self._recursive_dict_search(item, target_key)
                        if result is not None:
                            return result
        return None
