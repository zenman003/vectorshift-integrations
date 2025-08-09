# airtable.py
import base64
import logging
from typing import List, Optional

import httpx
from core import settings
from core.contracts import KeyValueStore
from fastapi import HTTPException, Request

from integrations.base import PKCEOAuthStrategy, oauth_close_window
from integrations.core import (
    AirtableCredentials,
    IntegrationItem,
    ItemType,
)

logger = logging.getLogger(__name__)


class AirtableAdapter:
    def __init__(self, http: httpx.AsyncClient, kv_store: KeyValueStore):
        """Initialize AirtableAdapter with OAuth and config."""
        self.authorization_url = f"https://airtable.com/oauth2/v1/authorize?client_id={settings.airtable_client_id}&response_type=code&owner=user&redirect_uri={settings.airtable_redirect_uri.replace(':', '%3A').replace('/', '%2F')}"
        self.encoded_client_id_secret = base64.b64encode(
            f"{settings.airtable_client_id}:{settings.airtable_client_secret}".encode()
        ).decode()
        self.http = http
        self.kv_store = kv_store
        self.oauth_strategy = PKCEOAuthStrategy("airtable", self.kv_store)

    async def authorize(self, user_id: str, org_id: str) -> str:
        """Return Airtable OAuth authorization URL with PKCE."""
        result = await self.oauth_strategy.authorize(
            user_id, org_id, settings.airtable_state_expiry_seconds
        )
        return f"{self.authorization_url}&state={result['encoded_state']}&code_challenge={result['code_challenge']}&code_challenge_method=S256&scope={settings.airtable_scope}"

    async def oauth_callback(self, request: Request):
        """Handle Airtable OAuth callback and store credentials."""
        result = await self.oauth_strategy.callback(dict(request.query_params))
        code, user_id, org_id, code_verifier = (
            result["code"],
            result["user_id"],
            result["org_id"],
            result["code_verifier"],
        )

        response = await self.http.post(
            "https://airtable.com/oauth2/v1/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.airtable_redirect_uri,
                "client_id": settings.airtable_client_id,
                "code_verifier": code_verifier,
            },
            headers={
                "Authorization": f"Basic {self.encoded_client_id_secret}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        if response.status_code != 200:
            logger.error(
                f"Failed to exchange code for token: status={response.status_code}"
            )
            raise HTTPException(
                status_code=400, detail="Failed to exchange code for token."
            )

        credentials = AirtableCredentials.model_validate(response.json())
        await self.kv_store.set(
            f"airtable_credentials:{org_id}:{user_id}",
            credentials.model_dump_json(),
            expire=settings.airtable_credentials_expiry_seconds,
        )
        return oauth_close_window()

    async def get_credentials(self, user_id: str, org_id: str):
        """Retrieve and delete Airtable credentials from Redis."""
        credentials = await self.kv_store.get(
            f"airtable_credentials:{org_id}:{user_id}"
        )
        if not credentials:
            raise HTTPException(status_code=400, detail="No credentials found.")
        credentials_data = AirtableCredentials.model_validate_json(credentials)
        await self.kv_store.delete(f"airtable_credentials:{org_id}:{user_id}")
        return credentials_data

    async def list_items(self, credentials: str) -> List[IntegrationItem]:
        """List Airtable bases and tables as IntegrationItems."""
        credentials_data = AirtableCredentials.model_validate_json(credentials)
        url = "https://api.airtable.com/v0/meta/bases"
        list_of_integration_item_metadata = []
        list_of_responses = []

        try:
            await self._fetch_items(
                credentials_data.access_token, url, list_of_responses
            )
            for response in list_of_responses:
                list_of_integration_item_metadata.append(
                    self._create_integration_item_metadata_object(response, "Base")
                )
                tables_response = await self.http.get(
                    f"https://api.airtable.com/v0/meta/bases/{response.get('id')}/tables",
                    headers={
                        "Authorization": f"Bearer {credentials_data.access_token}"
                    },
                )
                if tables_response.status_code == 200:
                    tables_response = tables_response.json()
                    for table in tables_response["tables"]:
                        list_of_integration_item_metadata.append(
                            self._create_integration_item_metadata_object(
                                table,
                                "Table",
                                response.get("id", None),
                                response.get("name", None),
                            )
                        )
                else:
                    logger.error(
                        f"Failed to fetch tables for base {response.get('id')}: {tables_response.status_code}"
                    )

            logger.info(
                f"Retrieved {len(list_of_integration_item_metadata)} integration items"
            )
            logger.info(f"Integration items: {list_of_integration_item_metadata}")
            return list_of_integration_item_metadata
        except Exception as err:
            logger.error(f"Error getting Airtable items: {err}")
            raise HTTPException(
                status_code=500, detail="Failed to retrieve Airtable items"
            )

    async def _fetch_items(
        self,
        access_token: str,
        url: str,
        aggregated_response: List[dict],
        offset: Optional[str] = None,
    ) -> None:
        """Fetch Airtable items with pagination using an iterative loop."""
        try:
            next_offset = offset
            headers = {"Authorization": f"Bearer {access_token}"}
            while True:
                params = {"offset": next_offset} if next_offset is not None else {}
                response = await self.http.get(url, headers=headers, params=params)

                if response.status_code != 200:
                    logger.error(
                        f"Failed to fetch items: status={response.status_code}"
                    )
                    break

                json_body = response.json()
                results = json_body.get("bases", {})
                for item in results:
                    aggregated_response.append(item)

                next_offset = json_body.get("offset")
                if not next_offset:
                    break
        except Exception as err:
            logger.error(f"Error fetching items: {err}")
            raise

    def _create_integration_item_metadata_object(
        self,
        response_json: dict,
        item_type: str,
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
    ) -> IntegrationItem:
        """Create IntegrationItem metadata object for Airtable base/table."""
        parent_id = None if parent_id is None else parent_id + "_Base"
        item_type_map = {
            "Base": ItemType.BASES,
            "Table": ItemType.TABLES,
        }
        mapped_type = item_type_map.get(item_type, ItemType.UNKNOWN)
        return IntegrationItem(
            id=f"{response_json.get('id') or 'unknown'}_{item_type}",
            name=response_json.get("name", None),
            type=mapped_type,
            parent_id=parent_id,
            parent_path_or_name=parent_name,
        )
