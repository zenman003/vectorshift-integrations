from datetime import datetime
from typing import List, Optional

from integrations.core.item_types import ItemType
from pydantic import AnyUrl
from pydantic.dataclasses import dataclass


@dataclass
class IntegrationItem:
    id: Optional[str] = None
    type: ItemType = ItemType.UNKNOWN
    directory: bool = False
    parent_path_or_name: Optional[str] = None
    parent_id: Optional[str] = None
    name: Optional[str] = None
    creation_time: Optional[datetime] = None
    last_modified_time: Optional[datetime] = None
    url: Optional[AnyUrl] = None
    children: Optional[List[str]] = None
    mime_type: Optional[str] = None
    delta: Optional[str] = None
    drive_id: Optional[str] = None
    visibility: Optional[bool] = True
