# hubspot.py
import asyncio
import base64
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

class HubSpotState(BaseModel):
    state: str
    user_id: str
    org_id: str

class HubSpotCredentials(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Optional[str] = None
    expires_in: Optional[int] = None

authorization_url = f'https://app.hubspot.com/oauth/authorize?client_id={settings.hubspot_client_id}&scope={settings.hubspot_scope}&redirect_uri={settings.hubspot_redirect_uri.replace(":", "%3A").replace("/", "%2F")}'

encoded_client_id_secret = base64.b64encode(f'{settings.hubspot_client_id}:{settings.hubspot_client_secret}'.encode()).decode()

async def authorize_hubspot(user_id: str, org_id: str) -> str:
    state_data = HubSpotState(
        state=secrets.token_urlsafe(32),
        user_id=user_id,
        org_id=org_id
    )
    encoded_state = base64.urlsafe_b64encode(state_data.model_dump_json().encode('utf-8')).decode('utf-8')

    auth_url = f'{authorization_url}&state={encoded_state}'
    await add_key_value_redis(f'hubspot_state:{org_id}:{user_id}', state_data.model_dump_json(), expire=settings.hubspot_state_expiry_seconds)

    return auth_url

async def oauth2callback_hubspot(request: Request) -> HTMLResponse:
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error_description'))
    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')
    state_data = HubSpotState.model_validate_json(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))

    original_state = state_data.state
    user_id = state_data.user_id
    org_id = state_data.org_id

    saved_state = await get_value_redis(f'hubspot_state:{org_id}:{user_id}')
    
    if not saved_state or HubSpotState.model_validate_json(saved_state).state != original_state:
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.hubapi.com/oauth/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'client_id': settings.hubspot_client_id,
                    'client_secret': settings.hubspot_client_secret,
                    'redirect_uri': settings.hubspot_redirect_uri,
                    'code': code
                }, 
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                }
            ),
            delete_key_redis(f'hubspot_state:{org_id}:{user_id}'),
        )

    if response.status_code != 200:
        logger.error(f"Failed to exchange code for token: {response.status_code} - {response.text}")
        raise HTTPException(status_code=500, detail="Failed to exchange authorization code for token")

    credentials = HubSpotCredentials.model_validate(response.json())
    await add_key_value_redis(f'hubspot_credentials:{org_id}:{user_id}', credentials.model_dump_json(), expire=settings.hubspot_credentials_expiry_seconds)
    
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)


async def get_hubspot_credentials(user_id: str, org_id: str) -> HubSpotCredentials:
    credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials_data = HubSpotCredentials.model_validate_json(credentials)
    await delete_key_redis(f'hubspot_credentials:{org_id}:{user_id}')

    return credentials_data

def create_integration_item_metadata_object(
    response_json: dict, item_type: str = 'contact', parent_id: Optional[str] = None, parent_name: Optional[str] = None
) -> IntegrationItem:
    pass

async def get_items_hubspot(credentials: str) -> List[IntegrationItem]:
    pass