"""Authentication helpers — secrets from Databricks secret scopes only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from uc_sync.security import redact


class SecretsProvider(Protocol):
    def get(self, scope: str, key: str) -> str: ...


@dataclass
class WorkspaceAuth:
    host: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None

    def __repr__(self) -> str:  # never print secrets
        return (
            f"WorkspaceAuth(host={self.host!r}, "
            f"client_id={'***' if self.client_id else None}, "
            f"has_secret={bool(self.client_secret)}, has_token={bool(self.token)})"
        )


def load_workspace_auth(
    host: str,
    scope: str,
    client_id_key: str,
    client_secret_key: str,
    secrets: SecretsProvider,
    token_key: str = "token",
) -> WorkspaceAuth:
    """Resolve OAuth client credentials or PAT from a secret scope."""
    if not host:
        raise ValueError("workspace host is required")
    if not scope:
        raise ValueError("secret scope is required for non-local auth")

    client_id = None
    client_secret = None
    token = None
    if client_id_key:
        try:
            client_id = secrets.get(scope, client_id_key)
        except Exception:
            client_id = None
    if client_secret_key:
        try:
            client_secret = secrets.get(scope, client_secret_key)
        except Exception:
            client_secret = None

    if not (client_id and client_secret):
        client_id = None
        client_secret = None
        try:
            token = secrets.get(scope, token_key)
        except Exception as exc:
            raise RuntimeError(
                redact(
                    f"No OAuth or token credentials found in scope '{scope}' "
                    f"(token key '{token_key}'): {exc}"
                )
            ) from exc

    if not ((client_id and client_secret) or token):
        raise RuntimeError(
            f"No OAuth or token credentials found in scope '{scope}'. "
            "Configure client id/secret keys or a token key."
        )

    return WorkspaceAuth(
        host=host.rstrip("/"),
        client_id=client_id,
        client_secret=client_secret,
        token=token,
    )


def direct_workspace_auth(
    host: str,
    client_id: str = "",
    client_secret: str = "",
    token: str = "",
) -> WorkspaceAuth:
    """Build auth from credential values supplied directly (not via a secret scope).

    Convenience for quick or one-off runs where creating a secret scope is
    overkill. Accepts either an OAuth ``client_id`` + ``client_secret`` pair or a
    PAT ``token``.

    SECURITY: values passed this way are carried in notebook widget values and job
    ``base_parameters`` (visible in the Jobs UI / API and not redacted like a
    ``{{secrets/...}}`` reference). Prefer :func:`load_workspace_auth` with a secret
    scope for anything shared or long-lived.
    """
    if not host:
        raise ValueError("workspace host is required")
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    token = (token or "").strip()
    if not ((client_id and client_secret) or token):
        raise RuntimeError(
            "direct auth requires either client_id + client_secret, or a token"
        )
    return WorkspaceAuth(
        host=host.rstrip("/"),
        client_id=client_id or None,
        client_secret=client_secret or None,
        token=token or None,
    )


def dbutils_secrets_provider(dbutils: Any) -> SecretsProvider:
    class _Provider:
        def get(self, scope: str, key: str) -> str:
            return dbutils.secrets.get(scope=scope, key=key)

    return _Provider()


def local_workspace_auth(dbutils: Any) -> WorkspaceAuth:
    """Use the notebook's short-lived current-workspace context."""
    context = (
        dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    )
    host = context.apiUrl().get()
    token = context.apiToken().get()
    if not host or not token:
        raise RuntimeError(
            "Current-workspace notebook authentication context is unavailable"
        )
    return WorkspaceAuth(host=host.rstrip("/"), token=token)
