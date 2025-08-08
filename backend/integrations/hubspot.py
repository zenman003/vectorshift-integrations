# hubspot.py
import asyncio
import base64
import json
import logging
import secrets
from typing import Optional, List
from datetime import datetime
from urllib.parse import quote

import httpx
import requests
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

from config import settings
from integrations.base.protocols import IntegrationAdapter
from integrations.base.oauth import StandardOAuthStrategy, oauth_close_window
from integrations.integration_item import IntegrationItem
from integrations.models import HubSpotCredentials
from integrations.registry import register_adapter
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

logger = logging.getLogger(__name__)

HUBSPOT_OBJECT_CONFIGS = {
    'contacts': {
        'properties': 'firstname,lastname,email,phone,company,createdate,lastmodifieddate',
        'name_fields': ['firstname', 'lastname'],
        'fallback_field': 'email',
        'singular_type': 'contact'
    },
    'companies': {
        'properties': 'name,domain,industry,city,state,country,createdate,lastmodifieddate',
        'name_fields': ['name'],
        'fallback_field': 'domain',
        'singular_type': 'company'
    },
    'deals': {
        'properties': 'dealname,amount,dealstage,closedate,pipeline,createdate,lastmodifieddate',
        'name_fields': ['dealname'],
        'fallback_field': 'amount',
        'singular_type': 'deal'
    }
}

class HubspotAdapter:
    def __init__(self):
        self.authorization_url = f'https://app.hubspot.com/oauth/authorize?client_id={settings.hubspot_client_id}&scope={settings.hubspot_scope.replace(" ", "%20")}&redirect_uri={settings.hubspot_redirect_uri.replace(":", "%3A").replace("/", "%2F")}'
        self.oauth_strategy = StandardOAuthStrategy('hubspot')

    async def authorize(self, user_id: str, org_id: str) -> str:
        """HubSpot OAuth strategy - standard OAuth 2.0 flow."""
        result = await self.oauth_strategy.authorize(user_id, org_id, settings.hubspot_state_expiry_seconds)
        return f'{self.authorization_url}&state={result["encoded_state"]}'

    async def oauth_callback(self, request: Request):
        """HubSpot OAuth callback strategy - standard OAuth 2.0 flow."""
        result = await self.oauth_strategy.callback(request)
        code, user_id, org_id = result['code'], result['user_id'], result['org_id']

        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://api.hubapi.com/oauth/v1/token',
                data={
                    'grant_type': 'authorization_code',
                    'client_id': settings.hubspot_client_id,
                    'client_secret': settings.hubspot_client_secret,
                    'redirect_uri': settings.hubspot_redirect_uri,
                    'code': code
                }, 
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )

        if response.status_code != 200:
            logger.error(f"Failed to exchange code for token: {response.status_code} - {response.text}")
            raise HTTPException(status_code=500, detail="Failed to exchange authorization code for token")

        credentials = HubSpotCredentials.model_validate(response.json())
        await add_key_value_redis(f'hubspot_credentials:{org_id}:{user_id}', credentials.model_dump_json(), expire=settings.hubspot_credentials_expiry_seconds)
        return oauth_close_window()

    async def get_credentials(self, user_id: str, org_id: str):
        """HubSpot credentials retrieval strategy."""
        credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
        if not credentials:
            raise HTTPException(status_code=400, detail='No credentials found.')
        credentials_data = HubSpotCredentials.model_validate_json(credentials)
        await delete_key_redis(f'hubspot_credentials:{org_id}:{user_id}')
        return credentials_data

    async def list_items(self, credentials: str) -> List[IntegrationItem]:
        """List HubSpot objects as IntegrationItems."""
        try:
            credentials_data = HubSpotCredentials.model_validate_json(credentials)
            list_of_integration_items = []
            
            object_types = list(HUBSPOT_OBJECT_CONFIGS.keys())
            
            for object_type in object_types:
                try:
                    list_of_responses = []
                    self._fetch_hubspot_objects(credentials_data.access_token, object_type, list_of_responses)
                    

                    config = HUBSPOT_OBJECT_CONFIGS[object_type]
                    item_type = config['singular_type']
                        
                    for response in list_of_responses:
                        list_of_integration_items.append(
                            self._create_integration_item_metadata_object(response, item_type)
                        )
                        
                    logger.info(f"Fetched {len(list_of_responses)} {object_type} from HubSpot")
                    
                except Exception as err:
                    logger.error(f"Error fetching {object_type}: {err}")
                    continue

            logger.info(f'Retrieved {len(list_of_integration_items)} total HubSpot integration items')
            return list_of_integration_items
            
        except Exception as err:
            logger.error(f"Error getting HubSpot items: {err}")
            raise HTTPException(status_code=500, detail="Failed to retrieve HubSpot items")

    @classmethod
    def _fetch_hubspot_objects(cls, access_token: str, object_type: str, aggregated_response: List[dict], after: Optional[str] = None) -> None:
        """Fetch HubSpot objects recursively with pagination."""
        try:
            config = HUBSPOT_OBJECT_CONFIGS.get(object_type, {})
            properties = config.get('properties', 'name')
            
            params = {
                'limit': 100,
                'properties': properties,
                'associations': 'contacts,companies,deals'
            }
            
            if after:
                params['after'] = after
                
            headers = {'Authorization': f'Bearer {access_token}'}
            response = requests.get(
                f'https://api.hubapi.com/crm/v3/objects/{object_type}',
                headers=headers,
                params=params
            )

            if response.status_code == 200:
                response_data = response.json()
                results = response_data.get('results', [])
                after_token = response_data.get('paging', {}).get('next', {}).get('after')

                for item in results:
                    aggregated_response.append(item)

                if after_token:
                    cls._fetch_hubspot_objects(access_token, object_type, aggregated_response, after_token)
                    
            elif response.status_code == 401:
                logger.error("Access token expired - refresh token needed")
                raise HTTPException(status_code=401, detail="Access token expired")
            else:
                logger.error(f"Failed to fetch {object_type}: {response.status_code} - {response.text}")
                
        except Exception as err:
            logger.error(f"Error fetching {object_type}: {err}")
            raise

    @classmethod
    def _create_integration_item_metadata_object(
        cls, response_json: dict, item_type: str = 'contact', parent_id: Optional[str] = None, parent_name: Optional[str] = None
    ) -> IntegrationItem:
        """Create IntegrationItem from HubSpot object response."""
        properties = response_json.get('properties', {})
        object_id = response_json.get('id', 'Unknown')
        
        config = None
        for obj_type, obj_config in HUBSPOT_OBJECT_CONFIGS.items():
            if obj_config['singular_type'] == item_type:
                config = obj_config
                break
        
        if config:
            name_parts = [properties.get(field) for field in config['name_fields'] if properties.get(field)]
            if name_parts:
                name = ' '.join(name_parts)
            else:
                fallback_value = properties.get(config['fallback_field'])
                if fallback_value:
                    name = str(fallback_value)
                else:
                    name = f"{item_type.title()} {object_id}"
        else:
            name = properties.get('name', f"{item_type.title()} {object_id}")

        def parse_hubspot_datetime(date_str: Optional[str]) -> Optional[datetime]:
            if not date_str:
                return None
            try:
                timestamp = int(date_str) / 1000
                return datetime.fromtimestamp(timestamp)
            except (ValueError, TypeError):
                return None
        
        is_directory = item_type == 'company'
        
        associations = response_json.get('associations', {})
        children = []
        if associations:
            for assoc_type, assoc_data in associations.items():
                if assoc_data and 'results' in assoc_data:
                    children.extend([result.get('id') for result in assoc_data['results'] if result.get('id')])
        
        archived = response_json.get('archived', False)
        visibility = not archived
        
        delta = response_json.get('updatedAt')
        
        url_path = f"{item_type}s"
        
        return IntegrationItem(
            id=object_id,
            type=item_type,
            directory=is_directory,
            parent_path_or_name=parent_name,
            parent_id=parent_id,
            name=name,
            creation_time=parse_hubspot_datetime(properties.get('createdate')),
            last_modified_time=parse_hubspot_datetime(properties.get('lastmodifieddate') or response_json.get('updatedAt')),
            url=f"https://app.hubspot.com/{url_path}/{object_id}",
            children=children if children else None,
            mime_type=f'application/vnd.hubspot.{item_type}',
            delta=delta,
            drive_id=None,
            visibility=visibility
        )


register_adapter('hubspot', HubspotAdapter())