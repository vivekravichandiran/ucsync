"""Thin UC REST client with pagination and backoff."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Optional

from uc_sync.auth import WorkspaceAuth
from uc_sync.security import redact


class WorkspaceClient:
    def __init__(self, auth: WorkspaceAuth, max_retries: int = 5):
        self.auth = auth
        self.max_retries = max_retries
        self._oauth_token: Optional[str] = None
        self._oauth_expires_at: float = 0.0

    def _exchange_oauth_token(self) -> str:
        """Exchange OAuth M2M client credentials for a bearer token."""
        if not (self.auth.client_id and self.auth.client_secret):
            raise RuntimeError("OAuth client id/secret are required for token exchange")
        now = time.time()
        if self._oauth_token and now < self._oauth_expires_at - 60:
            return self._oauth_token
        token_url = f"{self.auth.host.rstrip('/')}/oidc/v1/token"
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "scope": "all-apis",
                "client_id": self.auth.client_id,
                "client_secret": self.auth.client_secret,
            }
        ).encode()
        req = urllib.request.Request(
            token_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode() if hasattr(exc, "read") else str(exc)
            raise RuntimeError(
                redact(f"OAuth token exchange failed HTTP {exc.code}: {detail}")
            ) from exc
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("OAuth token exchange returned no access_token")
        expires_in = int(payload.get("expires_in") or 3600)
        self._oauth_token = token
        self._oauth_expires_at = now + expires_in
        return token

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth.token:
            headers["Authorization"] = f"Bearer {self.auth.token}"
        elif self.auth.client_id and self.auth.client_secret:
            headers["Authorization"] = f"Bearer {self._exchange_oauth_token()}"
        else:
            raise RuntimeError("WorkspaceAuth has neither token nor OAuth credentials")
        return headers

    def request(
        self,
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        query: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = self.auth.host + path
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None}
            )
        data = json.dumps(body).encode() if body is not None else None
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                url, data=data, method=method.upper(), headers=self._headers()
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read().decode() or "{}"
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as exc:
                payload = exc.read().decode() if hasattr(exc, "read") else str(exc)
                # Force a fresh OAuth token once on unauthorized responses.
                if (
                    exc.code == 401
                    and self.auth.client_id
                    and self.auth.client_secret
                    and attempt < self.max_retries - 1
                ):
                    self._oauth_token = None
                    self._oauth_expires_at = 0.0
                    last_err = RuntimeError(redact(f"HTTP {exc.code}: {payload}"))
                    continue
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_retries - 1:
                    time.sleep(min(2**attempt, 20))
                    last_err = RuntimeError(redact(f"HTTP {exc.code}: {payload}"))
                    continue
                raise RuntimeError(redact(f"HTTP {exc.code}: {payload}")) from exc
            except Exception as exc:  # noqa: BLE001
                last_err = RuntimeError(redact(str(exc)))
                time.sleep(min(2**attempt, 20))
        raise RuntimeError(redact(str(last_err) if last_err else "request failed"))

    def get(self, path: str, **query: Any) -> dict[str, Any]:
        return self.request("GET", path, query=query or None)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, body=body)

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", path, body=body)

    def delete(self, path: str, **query: Any) -> dict[str, Any]:
        return self.request("DELETE", path, query=query or None)

    def paginate(
        self,
        path: str,
        items_key: str,
        *,
        max_results: int = 100,
        **query: Any,
    ) -> Iterator[dict[str, Any]]:
        page_token: Optional[str] = None
        while True:
            q = dict(query)
            q["max_results"] = max_results
            if page_token:
                q["page_token"] = page_token
            data = self.get(path, **q)
            for item in data.get(items_key) or []:
                yield item
            page_token = data.get("next_page_token")
            if not page_token:
                break

    def current_metastore_assignment(self) -> dict[str, Any]:
        return self.get("/api/2.1/unity-catalog/current-metastore-assignment")


def build_sdk_client(auth: WorkspaceAuth) -> Any:
    """Preferred runtime client inside Databricks."""
    from databricks.sdk import WorkspaceClient as SdkWorkspaceClient

    if auth.token:
        return SdkWorkspaceClient(host=auth.host, token=auth.token)
    return SdkWorkspaceClient(
        host=auth.host,
        client_id=auth.client_id,
        client_secret=auth.client_secret,
    )
