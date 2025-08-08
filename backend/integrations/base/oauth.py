from __future__ import annotations

import base64
import secrets
from abc import ABC, abstractmethod

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

from integrations.models import OAuthState
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis


class OAuthStrategy(ABC):
    def __init__(self, provider: str):
        self.provider = provider
    
    @abstractmethod
    async def authorize(self, user_id: str, org_id: str, expiry_seconds: int) -> dict:
        """Generate OAuth authorization data."""
        pass
    
    @abstractmethod
    async def callback(self, request: Request) -> dict:
        """Handle OAuth callback and return verification data."""
        pass


class StandardOAuthStrategy(OAuthStrategy):
    async def authorize(self, user_id: str, org_id: str, expiry_seconds: int) -> dict:
        """Standard OAuth 2.0 authorize flow - returns state data and encoded state."""
        state_data = OAuthState(state=secrets.token_urlsafe(32), user_id=user_id, org_id=org_id)
        encoded_state = base64.urlsafe_b64encode(state_data.model_dump_json().encode('utf-8')).decode('utf-8')
        await add_key_value_redis(f'{self.provider}_state:{org_id}:{user_id}', state_data.model_dump_json(), expire=expiry_seconds)
        return {'state_data': state_data, 'encoded_state': encoded_state}

    async def callback(self, request: Request) -> dict:
        """Standard OAuth 2.0 callback verification - returns code, user_id, org_id."""
        if request.query_params.get('error'):
            raise HTTPException(status_code=400, detail=request.query_params.get('error_description', 'OAuth error'))
        
        code = request.query_params.get('code')
        if not code:
            raise HTTPException(status_code=400, detail='Authorization code not provided')
        
        encoded_state = request.query_params.get('state')
        if not encoded_state:
            raise HTTPException(status_code=400, detail='State parameter not provided')
        
        try:
            received_state_data = OAuthState.model_validate_json(base64.urlsafe_b64decode(encoded_state).decode('utf-8'))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f'Invalid state parameter: {str(e)}')
        
        user_id = received_state_data.user_id
        org_id = received_state_data.org_id
        
        saved_state_json = await get_value_redis(f'{self.provider}_state:{org_id}:{user_id}')
        if not saved_state_json:
            raise HTTPException(status_code=400, detail='State not found or expired')
        
        saved_state_data = OAuthState.model_validate_json(saved_state_json)
        if saved_state_data.state != received_state_data.state:
            raise HTTPException(status_code=400, detail='State mismatch')
        
        await delete_key_redis(f'{self.provider}_state:{org_id}:{user_id}')
        return {'code': code, 'user_id': user_id, 'org_id': org_id}


class PKCEOAuthStrategy(OAuthStrategy):
    async def authorize(self, user_id: str, org_id: str, expiry_seconds: int) -> dict:
        """PKCE OAuth 2.0 authorize flow - returns state data, encoded state, and code challenge."""
        import hashlib
        
        state_data = OAuthState(state=secrets.token_urlsafe(32), user_id=user_id, org_id=org_id)
        encoded_state = base64.urlsafe_b64encode(state_data.model_dump_json().encode('utf-8')).decode('utf-8')
        
        code_verifier = secrets.token_urlsafe(32)
        m = hashlib.sha256()
        m.update(code_verifier.encode('utf-8'))
        code_challenge = base64.urlsafe_b64encode(m.digest()).decode('utf-8').replace('=', '')
        
        await add_key_value_redis(f'{self.provider}_state:{org_id}:{user_id}', state_data.model_dump_json(), expire=expiry_seconds)
        await add_key_value_redis(f'{self.provider}_verifier:{org_id}:{user_id}', code_verifier, expire=expiry_seconds)
        
        return {'state_data': state_data, 'encoded_state': encoded_state, 'code_challenge': code_challenge}

    async def callback(self, request: Request) -> dict:
        """PKCE OAuth 2.0 callback verification - returns code, user_id, org_id, and code_verifier."""
        # First do standard verification using the base strategy
        standard_strategy = StandardOAuthStrategy(self.provider)
        result = await standard_strategy.callback(request)
        code, user_id, org_id = result['code'], result['user_id'], result['org_id']
        
        # Get code verifier
        code_verifier = await get_value_redis(f'{self.provider}_verifier:{org_id}:{user_id}')
        if not code_verifier:
            raise HTTPException(status_code=400, detail='Code verifier not found or expired')
        
        await delete_key_redis(f'{self.provider}_verifier:{org_id}:{user_id}')
        return {'code': code, 'user_id': user_id, 'org_id': org_id, 'code_verifier': code_verifier.decode('utf-8')}


def oauth_close_window():
    """Standard OAuth close window response."""
    return HTMLResponse(content="""
    <html>
        <script>
            window.close();
        </script>
    </html>
    """)
