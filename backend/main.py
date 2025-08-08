from fastapi import FastAPI, Form, Request, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware

import integrations.airtable 
import integrations.notion 
import integrations.hubspot  
from integrations.registry import get_adapter

app = FastAPI()

origins = [
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post('/integrations/{provider}/authorize')
async def authorize_integration(provider: str, user_id: str = Form(...), org_id: str = Form(...)):
    """Generic authorize endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.authorize(user_id, org_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.get('/integrations/{provider}/oauth2callback')
async def oauth_callback_integration(provider: str, request: Request):
    """Generic OAuth callback endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.oauth_callback(request)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.post('/integrations/{provider}/credentials')
async def get_credentials_integration(provider: str, user_id: str = Form(...), org_id: str = Form(...)):
    """Generic credentials endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.get_credentials(user_id, org_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.post('/integrations/{provider}/load')
async def get_items_integration(provider: str, credentials: str = Form(...)):
    """Generic load items endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.list_items(credentials)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")
