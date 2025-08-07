# notion.py
import asyncio
import base64
import datetime
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

class NotionState(BaseModel):
    state: str
    user_id: str
    org_id: str

class NotionCredentials(BaseModel):
    access_token: str
    token_type: Optional[str] = None
    bot_id: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_icon: Optional[str] = None
    owner: Optional[dict] = None

authorization_url = f'https://api.notion.com/v1/oauth/authorize?client_id={settings.notion_client_id}&response_type=code&owner=user&redirect_uri={settings.notion_redirect_uri.replace(":", "%3A").replace("/", "%2F")}'

encoded_client_id_secret = base64.b64encode(f'{settings.notion_client_id}:{settings.notion_client_secret}'.encode()).decode()

async def authorize_notion(user_id: str, org_id: str) -> str:
    state_data = NotionState(
        state=secrets.token_urlsafe(32),
        user_id=user_id,
        org_id=org_id
    )
    encoded_state = base64.urlsafe_b64encode(state_data.model_dump_json().encode('utf-8')).decode('utf-8')

    auth_url = f'{authorization_url}&state={encoded_state}'
    await add_key_value_redis(f'notion_state:{org_id}:{user_id}', state_data.model_dump_json(), expire=settings.notion_state_expiry_seconds)

    return auth_url

async def oauth2callback_notion(request: Request) -> HTMLResponse:
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error_description'))
    code = request.query_params.get('code')
    encoded_state = request.query_params.get('state')
    state_data = NotionState.model_validate_json(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))

    original_state = state_data.state
    user_id = state_data.user_id
    org_id = state_data.org_id

    saved_state = await get_value_redis(f'notion_state:{org_id}:{user_id}')

    if not saved_state or original_state != NotionState.model_validate_json(saved_state).state:
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.notion.com/v1/oauth/token',
                json={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': settings.notion_redirect_uri
                }, 
                headers={
                    'Authorization': f'Basic {encoded_client_id_secret}',
                    'Content-Type': 'application/json',
                }
            ),
            delete_key_redis(f'notion_state:{org_id}:{user_id}'),
        )

    credentials = NotionCredentials.model_validate(response.json())
    await add_key_value_redis(f'notion_credentials:{org_id}:{user_id}', credentials.model_dump_json(), expire=settings.notion_credentials_expiry_seconds)
    
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_notion_credentials(user_id: str, org_id: str) -> NotionCredentials:
    credentials = await get_value_redis(f'notion_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials_data = NotionCredentials.model_validate_json(credentials)
    await delete_key_redis(f'notion_credentials:{org_id}:{user_id}')

    return credentials_data

def _recursive_dict_search(data: dict, target_key: str) -> Optional[str]:
    """Recursively search for a key in a dictionary of dictionaries."""
    if not isinstance(data, dict):
        return None
        
    if target_key in data:
        return data[target_key]

    for value in data.values():
        if isinstance(value, dict):
            result = _recursive_dict_search(value, target_key)
            if result is not None:
                return result
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    result = _recursive_dict_search(item, target_key)
                    if result is not None:
                        return result
    return None

def create_integration_item_metadata_object(
    response_json: dict, item_type: str, parent_id: Optional[str] = None, parent_name: Optional[str] = None
) -> IntegrationItem:
    """Creates an internal dataclass from the response"""
    try:
        name = _recursive_dict_search(response_json.get('properties', {}), 'content')
        
        if response_json.get('parent', {}).get('type') == 'workspace':
            computed_parent_id = None
        else:
            computed_parent_id = parent_id

        name = _recursive_dict_search(response_json, 'content') if name is None else name
        name = 'multi_select' if name is None else name
        name = response_json.get('object', '') + ' ' + str(name)

        return IntegrationItem(
            id=response_json.get('id'),
            type=item_type or response_json.get('object'),
            name=name,
            creation_time=response_json.get('created_time'),
            last_modified_time=response_json.get('last_edited_time'),
            parent_id=computed_parent_id,
            parent_path_or_name=parent_name,
        )
    except Exception as err:
        logger.error(f"Error creating integration item metadata: {err}")
        
        return IntegrationItem(
            id=response_json.get('id', 'unknown'),
            type=item_type or response_json.get('object', 'unknown'),
            name=f"Error parsing {response_json.get('object', 'item')}"
        )

async def get_items_notion(credentials: str) -> List[IntegrationItem]:
    """External API function returning Pydantic models"""
    try:
        credentials_data = NotionCredentials.model_validate_json(credentials)
        response = requests.post(
            'https://api.notion.com/v1/search',
            headers={
                'Authorization': f'Bearer {credentials_data.access_token}',
                'Notion-Version': '2022-06-28',
            },
        )

        if response.status_code == 200:
            results = response.json()['results']
            internal_items: List[IntegrationItem] = []
            for result in results:
                item_type = result.get('object', 'unknown')
                parent_id = None
                parent_name = None
                
                if result.get('parent', {}).get('type') != 'workspace':
                    parent_type = result.get('parent', {}).get('type')
                    if parent_type and parent_type in result.get('parent', {}):
                        parent_id = result.get('parent', {}).get(parent_type)
                
                internal_items.append(
                    create_integration_item_metadata_object(result, item_type, parent_id, parent_name)
                )

            logger.info(f'Retrieved {len(internal_items)} integration items')
            
            return internal_items
        else:
            logger.error(f"Failed to fetch Notion items: {response.status_code} - {response.text}")
            raise HTTPException(status_code=500, detail="Failed to retrieve Notion items")
    except Exception as err:
        logger.error(f"Error getting Notion items: {err}")
        raise HTTPException(status_code=500, detail="Failed to retrieve Notion items")
