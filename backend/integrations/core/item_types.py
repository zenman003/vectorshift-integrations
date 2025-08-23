# backend/integrations/core/item_types.py
from enum import Enum


class ItemType(str, Enum):
    CONTACTS = "contacts"
    COMPANIES = "companies"
    DEALS = "deals"
    BASES = "bases"
    TABLES = "tables"
    PAGES = "pages"
    DATABASES = "databases"
    UNKNOWN = "unknown"


