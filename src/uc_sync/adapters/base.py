"""Adapter protocol for UC object types."""

from __future__ import annotations

from typing import Iterable, Optional, Protocol

from uc_sync.models import UCObject
from uc_sync.workspace_client import WorkspaceClient


class ObjectAdapter(Protocol):
    object_type: str

    def list(self, client: WorkspaceClient, **parents: str) -> Iterable[UCObject]: ...

    def get(self, client: WorkspaceClient, full_name: str) -> UCObject: ...

    def to_ddl(self, obj: UCObject) -> Optional[str]: ...
