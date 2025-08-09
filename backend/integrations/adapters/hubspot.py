# hubspot.py
import logging
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import HTTPException, Request

from core import settings
from core.contracts import KeyValueStore
from integrations.base import StandardOAuthStrategy, oauth_close_window
from integrations.core import (
    HubSpotCredentials,
    IntegrationItem,
    ItemType,
)

logger = logging.getLogger(__name__)

HUBSPOT_OBJECT_CONFIGS = {
    "contacts": {
        "properties": "firstname,lastname,email,phone,company,createdate,lastmodifieddate",
        "name_fields": ["firstname", "lastname"],
        "fallback_field": "email",
    },
    "companies": {
        "properties": "name,domain,industry,city,state,country,createdate,lastmodifieddate",
        "name_fields": ["name"],
        "fallback_field": "domain",
    },
    "deals": {
        "properties": "dealname,amount,dealstage,closedate,pipeline,createdate,lastmodifieddate",
        "name_fields": ["dealname"],
        "fallback_field": "amount",
    },
}


class HubspotAdapter:
    def __init__(self, http: httpx.AsyncClient, kv_store: KeyValueStore):
        self.authorization_url = f"https://app.hubspot.com/oauth/authorize?client_id={settings.hubspot_client_id}&scope={settings.hubspot_scope.replace(' ', '%20')}&redirect_uri={settings.hubspot_redirect_uri.replace(':', '%3A').replace('/', '%2F')}"
        self.http = http
        self.kv_store = kv_store
        self.oauth_strategy = StandardOAuthStrategy("hubspot", self.kv_store)

    async def authorize(self, user_id: str, org_id: str) -> str:
        """HubSpot OAuth strategy - standard OAuth 2.0 flow."""
        result = await self.oauth_strategy.authorize(
            user_id, org_id, settings.hubspot_state_expiry_seconds
        )
        return f"{self.authorization_url}&state={result['encoded_state']}"

    async def oauth_callback(self, request: Request):
        """HubSpot OAuth callback strategy - standard OAuth 2.0 flow."""
        result = await self.oauth_strategy.callback(dict(request.query_params))
        code, user_id, org_id = result["code"], result["user_id"], result["org_id"]

        response = await self.http.post(
            "https://api.hubapi.com/oauth/v1/token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.hubspot_client_id,
                "client_secret": settings.hubspot_client_secret,
                "redirect_uri": settings.hubspot_redirect_uri,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            logger.error(
                f"Failed to exchange code for token: status={response.status_code}"
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to exchange authorization code for token",
            )

        credentials = HubSpotCredentials.model_validate(response.json())
        await self.kv_store.set(
            f"hubspot_credentials:{org_id}:{user_id}",
            credentials.model_dump_json(),
            expire=settings.hubspot_credentials_expiry_seconds,
        )
        return oauth_close_window()

    async def get_credentials(self, user_id: str, org_id: str):
        """HubSpot credentials retrieval strategy."""
        credentials = await self.kv_store.get(f"hubspot_credentials:{org_id}:{user_id}")
        if not credentials:
            raise HTTPException(status_code=400, detail="No credentials found.")
        credentials_data = HubSpotCredentials.model_validate_json(credentials)
        await self.kv_store.delete(f"hubspot_credentials:{org_id}:{user_id}")
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
                    await self._fetch_hubspot_objects(
                        credentials_data.access_token, object_type, list_of_responses
                    )

                    _ = HUBSPOT_OBJECT_CONFIGS[object_type]
                    item_type = object_type

                    for response in list_of_responses:
                        list_of_integration_items.append(
                            self._create_integration_item_metadata_object(
                                response, item_type
                            )
                        )

                    logger.info(
                        f"Fetched {len(list_of_responses)} {object_type} from HubSpot"
                    )

                except Exception as err:
                    logger.error(f"Error fetching {object_type}: {err}")
                    continue

            logger.info(
                f"Retrieved {len(list_of_integration_items)} total HubSpot integration items"
            )
            logger.info(f"HubSpot integration items: {list_of_integration_items}")
            return list_of_integration_items

        except Exception as err:
            logger.error(f"Error getting HubSpot items: {err}")
            raise HTTPException(
                status_code=500, detail="Failed to retrieve HubSpot items"
            )

    async def _fetch_hubspot_objects(
        self,
        access_token: str,
        object_type: str,
        aggregated_response: List[dict],
        after: Optional[str] = None,
    ) -> None:
        """Fetch HubSpot objects recursively with pagination."""
        try:
            config = HUBSPOT_OBJECT_CONFIGS.get(object_type, {})
            properties = config.get("properties", "name")

            params = {
                "limit": 100,
                "properties": properties,
                "associations": "contacts,companies,deals",
            }

            if after:
                params["after"] = after

            headers = {"Authorization": f"Bearer {access_token}"}
            response = await self.http.get(
                f"https://api.hubapi.com/crm/v3/objects/{object_type}",
                headers=headers,
                params=params,
            )

            if response.status_code == 200:
                response_data = response.json()
                results = response_data.get("results", [])
                after_token = (
                    response_data.get("paging", {}).get("next", {}).get("after")
                )

                for item in results:
                    aggregated_response.append(item)

                if after_token:
                    while after_token:
                        params["after"] = after_token
                        response = await self.http.get(
                            f"https://api.hubapi.com/crm/v3/objects/{object_type}",
                            headers=headers,
                            params=params,
                        )
                        if response.status_code != 200:
                            logger.error(
                                f"Failed to fetch {object_type}: status={response.status_code}"
                            )
                            break
                        response_data = response.json()
                        results = response_data.get("results", [])
                        for item in results:
                            aggregated_response.append(item)
                        after_token = (
                            response_data.get("paging", {}).get("next", {}).get("after")
                        )

            elif response.status_code == 401:
                logger.error("Access token expired - refresh token needed")
                raise HTTPException(status_code=401, detail="Access token expired")
            else:
                logger.error(
                    f"Failed to fetch {object_type}: status={response.status_code}"
                )

        except Exception as err:
            logger.error(f"Error fetching {object_type}: {err}")
            raise

    def _create_integration_item_metadata_object(
        self,
        response_json: dict,
        item_type: str = "contacts",
        parent_id: Optional[str] = None,
        parent_name: Optional[str] = None,
    ) -> IntegrationItem:
        """Create IntegrationItem from HubSpot object response."""
        properties = response_json.get("properties", {})
        object_id = response_json.get("id", "Unknown")

        config = HUBSPOT_OBJECT_CONFIGS.get(item_type, {})

        if config:
            name_parts = [
                properties.get(field)
                for field in config["name_fields"]
                if properties.get(field)
            ]
            if name_parts:
                name = " ".join(name_parts)
            else:
                fallback_value = properties.get(config["fallback_field"])
                if fallback_value:
                    name = str(fallback_value)
                else:
                    name = f"{item_type.title()} {object_id}"
        else:
            name = properties.get("name", f"{item_type.title()} {object_id}")

        def parse_hubspot_datetime(date_str: Optional[str]) -> Optional[datetime]:
            if not date_str:
                return None
            try:
                if isinstance(date_str, int) or (
                    isinstance(date_str, str)
                    and date_str.isdigit()
                    and len(date_str) == 13
                ):
                    timestamp = int(date_str) / 1000
                    return datetime.fromtimestamp(timestamp)
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except Exception:
                return None

        is_directory = item_type == ItemType.COMPANIES

        associations = response_json.get("associations", {})
        children = []
        if associations:
            for assoc_type, assoc_data in associations.items():
                if assoc_data and "results" in assoc_data:
                    children.extend(
                        [
                            result.get("id")
                            for result in assoc_data["results"]
                            if result.get("id")
                        ]
                    )

        archived = response_json.get("archived", False)
        visibility = not archived

        delta = response_json.get("updatedAt")

        url_path = item_type

        try:
            item_type_enum = ItemType(item_type)
        except ValueError:
            item_type_enum = ItemType.UNKNOWN

        return IntegrationItem(
            id=object_id,
            type=item_type_enum,
            directory=is_directory,
            parent_path_or_name=parent_name,
            parent_id=parent_id,
            name=name,
            creation_time=parse_hubspot_datetime(properties.get("createdate")),
            last_modified_time=parse_hubspot_datetime(
                properties.get("lastmodifieddate") or response_json.get("updatedAt")
            ),
            url=f"https://app.hubspot.com/{url_path}/{object_id}",
            children=children if children else None,
            mime_type=f"application/vnd.hubspot.{item_type}",
            delta=delta,
            drive_id=None,
            visibility=visibility,
        )


# Registration moved to main where dependencies are injected
