# backend/main.py
import logging

import httpx
from core import set_client
from core.redis_store import RedisStore
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from integrations.adapters.airtable import AirtableAdapter
from integrations.adapters.hubspot import HubspotAdapter
from integrations.adapters.notion import NotionAdapter
from integrations.core import get_adapter, register_adapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def register_adapters_with_dependencies(client: httpx.AsyncClient):
    kv = RedisStore()
    register_adapter("airtable", AirtableAdapter(client, kv))
    register_adapter("hubspot", HubspotAdapter(client, kv))
    register_adapter("notion", NotionAdapter(client, kv))


async def lifespan(app: FastAPI):
    client = httpx.AsyncClient(
        timeout=10.0,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        headers={"User-Agent": "vectorshift-integrations/1.0"},
    )
    set_client(client)

    register_adapters_with_dependencies(client)

    yield

    await client.aclose()


app = FastAPI(lifespan=lifespan)
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


@app.post("/integrations/{provider}/authorize")
async def authorize_integration(
    provider: str, user_id: str = Form(...), org_id: str = Form(...)
):
    """Generic authorize endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.authorize(user_id, org_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.get("/integrations/{provider}/oauth2callback")
async def oauth_callback_integration(provider: str, request: Request):
    """Generic OAuth callback endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.oauth_callback(request)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.post("/integrations/{provider}/credentials")
async def get_credentials_integration(
    provider: str, user_id: str = Form(...), org_id: str = Form(...)
):
    """Generic credentials endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.get_credentials(user_id, org_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")


@app.post("/integrations/{provider}/load")
async def get_items_integration(provider: str, credentials: str = Form(...)):
    """Generic load items endpoint for any registered provider."""
    try:
        adapter = get_adapter(provider)
        return await adapter.list_items(credentials)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")
