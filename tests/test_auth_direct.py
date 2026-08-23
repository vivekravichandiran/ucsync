"""Direct-value credential auth (alternative to secret-scope references)."""

from __future__ import annotations

import pytest

from uc_sync.auth import direct_workspace_auth
from uc_sync.config import from_sources


def test_direct_auth_with_client_id_and_secret():
    auth = direct_workspace_auth(
        "https://src.example.com/", client_id="cid", client_secret="csecret"
    )
    assert auth.host == "https://src.example.com"
    assert auth.client_id == "cid"
    assert auth.client_secret == "csecret"
    assert auth.token is None
    # secrets never appear in repr
    assert "csecret" not in repr(auth)


def test_direct_auth_with_token():
    auth = direct_workspace_auth("https://src.example.com", token="dapiXXXX")
    assert auth.token == "dapiXXXX"
    assert auth.client_id is None


def test_direct_auth_requires_some_credential():
    with pytest.raises(RuntimeError):
        direct_workspace_auth("https://src.example.com")


def test_direct_auth_requires_host():
    with pytest.raises(ValueError):
        direct_workspace_auth("", client_id="cid", client_secret="csecret")


def test_config_carries_direct_credential_values():
    cfg = from_sources(
        {
            "stage": "INVENTORY",
            "output_volume_path": "/Volumes/a/b/c",
            "ops_catalog": "a",
            "ops_schema": "b",
            "source_workspace_url": "https://src.example.com",
            "source_client_id": "cid",
            "source_client_secret": "csecret",
        }
    )
    assert cfg.source_client_id == "cid"
    assert cfg.source_client_secret == "csecret"
    assert cfg.source_token == ""
