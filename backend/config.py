# config.py
from pydantic import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Airtable Configuration
    airtable_client_id: str
    airtable_client_secret: str
    airtable_redirect_uri: str
    airtable_state_expiry_seconds: int = 600
    airtable_credentials_expiry_seconds: int = 600
    airtable_scope: str = 'data.records:read data.records:write data.recordComments:read data.recordComments:write schema.bases:read schema.bases:write'

    # Notion Configuration
    notion_client_id: str
    notion_client_secret: str
    notion_redirect_uri: str
    notion_state_expiry_seconds: int = 600
    notion_credentials_expiry_seconds: int = 600

    # HubSpot Configuration
    hubspot_client_id: str
    hubspot_client_secret: str
    hubspot_redirect_uri: str
    hubspot_scope: str = 'crm.objects.contacts.read oauth'
    hubspot_state_expiry_seconds: int = 600
    hubspot_credentials_expiry_seconds: int = 600

    # Redis Configuration
    redis_ttl_default: int = 3600

    # OAuth Configuration
    oauth_state_length: int = 32
    oauth_code_verifier_length: int = 32

    model_config = SettingsConfigDict(env_file = '.env')


settings = Settings()
