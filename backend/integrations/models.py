from pydantic import BaseModel
from typing import Optional


class OAuthState(BaseModel):
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


class HubSpotCredentials(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Optional[str] = None
    expires_in: Optional[int] = None


class AirtableCredentials(BaseModel):
    access_token: str
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
