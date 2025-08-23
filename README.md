# Vectorshift Integrations – Summary

This project provides a unified FastAPI backend + lightweight React frontend for integrating Airtable, Notion, and HubSpot via a normalized item model.

## 1. Initial Issues (Before Refactor)

- Mixed sync + async HTTP (`requests` inside async) causing blocking.
- Hard‑coded OAuth secrets.
- Repetitive per‑provider routes & OAuth logic (no abstraction).
- Inconsistent / missing type hints & data models.
- No centralized config
- Lacked logging, structured error handling, and normalization layer.
- Recursive / ad‑hoc pagination patterns.

## 2. What Changed and Why

Originally, the backend was a mix of blocking and non-blocking HTTP calls, with secrets hard-coded in the source and nearly identical route handlers for each provider. OAuth logic was repeated everywhere, pagination was recursive, and error handling was inconsistent. There was no clear data model for frontend use.

The refactor made everything more consistent, secure, and easier to extend:

- Every provider now uses the same adapter pattern. Each adapter knows how to build an authorization URL, handle OAuth callbacks, return credentials, and list items. The API layer just routes requests to the right adapter automatically.
- OAuth differences (like PKCE for Airtable) are handled by strategy classes, so security checks and state handling are predictable and not duplicated.
- Secrets and config values are loaded from environment variables, not hard-coded. Temporary OAuth state and credentials are stored in Redis and expire quickly, so sensitive data isn't kept longer than needed.
- All HTTP calls are async and use a shared client, so the app is fast and doesn't block this is recommended on httpx docs
  this has more funtionality rather than creating a new client for each request.
- There's a normalized "IntegrationItem" model and an enum for item types, so the frontend can treat all provider data the same way. Each adapter maps raw API responses into this shape, this has been made @dataclass, primarily for easier integration with third-party tools.
  further dataclass is faster than basemodel
- Error handling and logging are much better: failures are logged clearly and return proper HTTP errors, AI was able to effectively handle errors, and the code is more readable.
- Pagination is now iterative, not recursive, making it safer and easier to debug.
- The directory structure is organized: core infrastructure (config, redis, http client), integration base classes, models, and adapters are all in their own places, again refactoring was made easier using AI.
- Adding a new provider is simple: create an adapter, implement four methods, register it, and you're done, this keeps it more extensible and maintainable.
- Security is improved by removing inline secrets, enforcing state verification, using PKCE, purging credentials after use, and not logging tokens, right now .env variables are used for sensitive data, for production you can use a secrets manager like AWS Secrets Manager or HashiCorp Vault.

In short: the codebase went from "works but messy and repetitive" to "modular, secure, and extensible," with a clear path for future improvements.

## 2.5 Dependency Injection (DI)

- What is injected

  - httpx.AsyncClient: one shared client created in app lifespan and passed to adapters
  - KeyValueStore: storage abstraction implemented by RedisStore
  - Settings: imported from core; no secrets in code

- How it’s wired

  - Adapters require explicit deps:
    - AirtableAdapter(http: httpx.AsyncClient, kv_store: KeyValueStore)
    - HubspotAdapter(http: httpx.AsyncClient, kv_store: KeyValueStore)
    - NotionAdapter(http: httpx.AsyncClient, kv_store: KeyValueStore)
  - OAuth strategies depend only on provider + KeyValueStore; they accept query params mapping (no FastAPI Request).
  - Registration happens in lifespan in `backend/main.py` by constructing deps once and registering adapters, lifespan funtion is current recommendated way to do starup set and cleanup on fast api docs


## 3. Data Model Snapshot

`IntegrationItem` unifies heterogeneous objects (bases, tables, pages, databases, contacts, companies, deals) with optional parent, timestamps, visibility, and type enum.

## 4. FastAPI Conventions Adopted

- Lifespan for shared `httpx.AsyncClient`.
- Async endpoints only; form inputs where needed.
- Single dynamic route set instead of per‑provider duplication.
- Pydantic models for OAuth state + credentials.
- CORS restricted to known dev origin.

## 5. Security Improvements

- Secrets exclusively via environment variables.
- PKCE for Airtable; standardized state handling for all providers.
- Ephemeral credentials purged after retrieval.
- Avoid logging raw tokens.

## 6. Performance Considerations

- Connection pooling (shared client).
- Selective property retrieval in HubSpot.
- Iterative pagination prevents deep stacks.

## 7. Local Development

Backend: `python3 -m uvicorn main:app --port 8000 --reload`
Frontend: `npm install && npm start`
Requires Redis running (default host configurable via env).

## 8. Environment Variables (Sample Keys)

```
AIRTABLE_CLIENT_ID= / AIRTABLE_CLIENT_SECRET= / AIRTABLE_REDIRECT_URI=
NOTION_CLIENT_ID= / NOTION_CLIENT_SECRET= / NOTION_REDIRECT_URI=
HUBSPOT_CLIENT_ID= / HUBSPOT_CLIENT_SECRET= / HUBSPOT_REDIRECT_URI=
REDIS_HOST=localhost
```

## 10. Provider Onboarding Workflow

1. Create new `XYZAdapter` implementing adapter protocol.
2. Implement authorize → callback → credentials → list methods.
3. Wire it in `backend/main.py` inside `register_adapters_with_dependencies(client)` by passing the shared `httpx.AsyncClient` and a `KeyValueStore` implementation.
4. Frontend automatically leverages generic endpoints.

