# airtable.py
import asyncio
import base64
import datetime
import hashlib
import json
import logging
import secrets
from typing import Optional, List

import httpx
import requests
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from config import settings
from integrations.integration_item import IntegrationItem
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis


logger = logging.getLogger(__name__)

class AirtableState(BaseModel):
    state: str
    user_id: str
    org_id: str

class AirtableCredentials(BaseModel):
    access_token: str
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None


authorization_url = f'https://airtable.com/oauth2/v1/authorize?client_id={settings.airtable_client_id}&response_type=code&owner=user&redirect_uri={settings.airtable_redirect_uri.replace(":", "%3A").replace("/", "%2F")}'

encoded_client_id_secret = base64.b64encode(f'{settings.airtable_client_id}:{settings.airtable_client_secret}'.encode()).decode()

async def authorize_airtable(user_id: str, org_id: str) -> str:
    state_data = AirtableState(
        state=secrets.token_urlsafe(32),
        user_id=user_id,
        org_id=org_id
    )
    encoded_state = base64.urlsafe_b64encode(state_data.model_dump_json().encode('utf-8')).decode('utf-8')

    code_verifier = secrets.token_urlsafe(32)
    m = hashlib.sha256()
    m.update(code_verifier.encode('utf-8'))
    code_challenge = base64.urlsafe_b64encode(m.digest()).decode('utf-8').replace('=', '')

    auth_url = f'{authorization_url}&state={encoded_state}&code_challenge={code_challenge}&code_challenge_method=S256&scope={settings.airtable_scope}'
    await asyncio.gather(
        add_key_value_redis(f'airtable_state:{org_id}:{user_id}', state_data.model_dump_json(), expire=settings.airtable_state_expiry_seconds),
        add_key_value_redis(f'airtable_verifier:{org_id}:{user_id}', code_verifier, expire=settings.airtable_state_expiry_seconds),
    )

    return auth_url

async def oauth2callback_airtable(request: Request) -> HTMLResponse:
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error_description'))
    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')
    state_data = AirtableState.model_validate_json(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))

    original_state = state_data.state
    user_id = state_data.user_id
    org_id = state_data.org_id

    saved_state, code_verifier = await asyncio.gather(
        get_value_redis(f'airtable_state:{org_id}:{user_id}'),
        get_value_redis(f'airtable_verifier:{org_id}:{user_id}'),
    )

    if not saved_state or original_state != AirtableState.model_validate_json(saved_state).state:
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _, _ = await asyncio.gather(
            client.post(
                'https://airtable.com/oauth2/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': settings.airtable_redirect_uri,
                    'client_id': settings.airtable_client_id,
                    'code_verifier': code_verifier.decode('utf-8'),
                },
                headers={
                    'Authorization': f'Basic {encoded_client_id_secret}',
                    'Content-Type': 'application/x-www-form-urlencoded',
                }
            ),
            delete_key_redis(f'airtable_state:{org_id}:{user_id}'),
            delete_key_redis(f'airtable_verifier:{org_id}:{user_id}'),
        )

    await add_key_value_redis(f'airtable_credentials:{org_id}:{user_id}', json.dumps(response.json()), expire=settings.airtable_credentials_expiry_seconds)
    
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_airtable_credentials(user_id: str, org_id: str) -> AirtableCredentials:
    credentials = await get_value_redis(f'airtable_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials_data = AirtableCredentials.model_validate_json(credentials)
    await delete_key_redis(f'airtable_credentials:{org_id}:{user_id}')

    return credentials_data

def create_integration_item_metadata_object(
    response_json: dict, item_type: str, parent_id: Optional[str] = None, parent_name: Optional[str] = None
) -> IntegrationItem:
    parent_id = None if parent_id is None else parent_id + '_Base'
    integration_item_metadata = IntegrationItem(
        id=response_json.get('id', None) + '_' + item_type,
        name=response_json.get('name', None),
        type=item_type,
        parent_id=parent_id,
        parent_path_or_name=parent_name,
    )

    return integration_item_metadata


def fetch_items(
    access_token: str, url: str, aggregated_response: List[dict], offset: Optional[str] = None
) -> None:
    """Fetching the list of bases"""
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
                fetch_items(access_token, url, aggregated_response, offset)
        else:
            logger.error(f"Failed to fetch items: {response.status_code} - {response.text}")
    except Exception as err:
        logger.error(f"Error fetching items: {err}")
        raise


async def get_items_airtable(credentials: str) -> List[IntegrationItem]:
    credentials_data = AirtableCredentials.model_validate_json(credentials)
    url = 'https://api.airtable.com/v0/meta/bases'
    list_of_integration_item_metadata = []
    list_of_responses = []

    try:
        fetch_items(credentials_data.access_token, url, list_of_responses)
        for response in list_of_responses:
            list_of_integration_item_metadata.append(
                create_integration_item_metadata_object(response, 'Base')
            )
            tables_response = requests.get(
                f'https://api.airtable.com/v0/meta/bases/{response.get("id")}/tables',
                headers={'Authorization': f'Bearer {credentials_data.access_token}'},
            )
            if tables_response.status_code == 200:
                tables_response = tables_response.json()
                for table in tables_response['tables']:
                    list_of_integration_item_metadata.append(
                        create_integration_item_metadata_object(
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
