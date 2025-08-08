# airtable.py
import asyncio
import base64
import hashlib
import logging
import secrets
from typing import Optional, List

import httpx
import requests
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

from config import settings
from integrations.base.protocols import IntegrationAdapter
from integrations.base.oauth import PKCEOAuthStrategy, oauth_close_window
from integrations.integration_item import IntegrationItem
from integrations.models import AirtableCredentials
from integrations.registry import register_adapter
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

logger = logging.getLogger(__name__)


class AirtableAdapter:
    def __init__(self):
        """Initialize AirtableAdapter with OAuth and config."""
        self.authorization_url = f'https://airtable.com/oauth2/v1/authorize?client_id={settings.airtable_client_id}&response_type=code&owner=user&redirect_uri={settings.airtable_redirect_uri.replace(":", "%3A").replace("/", "%2F")}'
        self.encoded_client_id_secret = base64.b64encode(f'{settings.airtable_client_id}:{settings.airtable_client_secret}'.encode()).decode()
        self.oauth_strategy = PKCEOAuthStrategy('airtable')

    async def authorize(self, user_id: str, org_id: str) -> str:
        """Return Airtable OAuth authorization URL with PKCE."""
        result = await self.oauth_strategy.authorize(user_id, org_id, settings.airtable_state_expiry_seconds)
        return f'{self.authorization_url}&state={result["encoded_state"]}&code_challenge={result["code_challenge"]}&code_challenge_method=S256&scope={settings.airtable_scope}'

    async def oauth_callback(self, request: Request):
        """Handle Airtable OAuth callback and store credentials."""
        result = await self.oauth_strategy.callback(request)
        code, user_id, org_id, code_verifier = result['code'], result['user_id'], result['org_id'], result['code_verifier']

        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://airtable.com/oauth2/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': settings.airtable_redirect_uri,
                    'client_id': settings.airtable_client_id,
                    'code_verifier': code_verifier,
                },
                headers={
                    'Authorization': f'Basic {self.encoded_client_id_secret}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                }
            )
            
        if response.status_code != 200:
            logger.error(f"Failed to exchange code for token: {response.status_code} - {response.text}")
            raise HTTPException(status_code=400, detail='Failed to exchange code for token.')
            
        credentials = AirtableCredentials.model_validate(response.json())
        await add_key_value_redis(f'airtable_credentials:{org_id}:{user_id}', credentials.model_dump_json(), expire=settings.airtable_credentials_expiry_seconds)
        return oauth_close_window()

    async def get_credentials(self, user_id: str, org_id: str):
        """Retrieve and delete Airtable credentials from Redis."""
        credentials = await get_value_redis(f'airtable_credentials:{org_id}:{user_id}')
        if not credentials:
            raise HTTPException(status_code=400, detail='No credentials found.')
        credentials_data = AirtableCredentials.model_validate_json(credentials)
        await delete_key_redis(f'airtable_credentials:{org_id}:{user_id}')
        return credentials_data

    async def list_items(self, credentials: str) -> List[IntegrationItem]:
        """List Airtable bases and tables as IntegrationItems."""
        credentials_data = AirtableCredentials.model_validate_json(credentials)
        url = 'https://api.airtable.com/v0/meta/bases'
        list_of_integration_item_metadata = []
        list_of_responses = []

        try:
            self._fetch_items(credentials_data.access_token, url, list_of_responses)
            for response in list_of_responses:
                list_of_integration_item_metadata.append(
                    self._create_integration_item_metadata_object(response, 'Base')
                )
                tables_response = requests.get(
                    f'https://api.airtable.com/v0/meta/bases/{response.get("id")}/tables',
                    headers={'Authorization': f'Bearer {credentials_data.access_token}'},
                )
                if tables_response.status_code == 200:
                    tables_response = tables_response.json()
                    for table in tables_response['tables']:
                        list_of_integration_item_metadata.append(
                            self._create_integration_item_metadata_object(
                                table,
                                'Table',
                                response.get('id', None),
                                response.get('name', None),
                            )
                        )
                else:
                    logger.error(f"Failed to fetch tables for base {response.get('id')}: {tables_response.status_code}")

            logger.info(f'Retrieved {len(list_of_integration_item_metadata)} integration items')
            return list_of_integration_item_metadata
        except Exception as err:
            logger.error(f"Error getting Airtable items: {err}")
            raise HTTPException(status_code=500, detail="Failed to retrieve Airtable items")

    @classmethod
    def _fetch_items(
        cls, access_token: str, url: str, aggregated_response: List[dict], offset: Optional[str] = None
    ) -> None:
        """Recursively fetch Airtable items with pagination."""
        try:
            params = {'offset': offset} if offset is not None else {}
            headers = {'Authorization': f'Bearer {access_token}'}
            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                results = response.json().get('bases', {})
                offset = response.json().get('offset', None)

                for item in results:
                    aggregated_response.append(item)

                if offset is not None:
                    cls._fetch_items(access_token, url, aggregated_response, offset)
            else:
                logger.error(f"Failed to fetch items: {response.status_code} - {response.text}")
        except Exception as err:
            logger.error(f"Error fetching items: {err}")
            raise

    @classmethod
    def _create_integration_item_metadata_object(
        cls, response_json: dict, item_type: str, parent_id: Optional[str] = None, parent_name: Optional[str] = None
    ) -> IntegrationItem:
        """Create IntegrationItem metadata object for Airtable base/table."""
        parent_id = None if parent_id is None else parent_id + '_Base'
        return IntegrationItem(
            id=response_json.get('id', None) + '_' + item_type,
            name=response_json.get('name', None),
            type=item_type,
            parent_id=parent_id,
            parent_path_or_name=parent_name,
        )


register_adapter('airtable', AirtableAdapter())
