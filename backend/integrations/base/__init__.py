# backend/integrations/base/__init__.py
from .oauth import PKCEOAuthStrategy, StandardOAuthStrategy, oauth_close_window
from .protocols import IntegrationAdapter

__all__ = [
    "PKCEOAuthStrategy",
    "StandardOAuthStrategy",
    "oauth_close_window",
    "IntegrationAdapter",
]
