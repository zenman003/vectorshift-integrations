# Base classes and protocols for integrations
from .oauth import PKCEOAuthStrategy, StandardOAuthStrategy, oauth_close_window
from .protocols import IntegrationAdapter

__all__ = [
    "PKCEOAuthStrategy",
    "StandardOAuthStrategy",
    "oauth_close_window",
    "IntegrationAdapter",
]
