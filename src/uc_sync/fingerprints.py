"""Per-object fingerprints for incremental (delta) sync (task 1).

An incremental run compares three fingerprints per source object against the
baseline stored in ``uc_sync_state`` and applies **only** what changed:

* ``ddl_fingerprint``        — the object's own structural DDL (columns, types, view
  text, function body, storage, and inline classic masks / row filters, which are
  part of ``CREATE TABLE``). A change here is a DDL change.
* ``governance_fingerprint`` — governance applied *after* create via ``ALTER … SET
  TAGS``: object-level tags + per-column tags. (ABAC policies are separate objects
  tracked by their own ``ddl_fingerprint``.) A change here → ``GOVERNANCE_UPDATED``,
  even when the DDL is unchanged.
* ``grant_fingerprint``      — the explicit (directly-assigned) grant set. Inherited
  privileges are never fingerprinted (they are re-established from parent grants).

All three are stable hashes of a canonicalised (sorted-key) JSON payload, so they
are comparable across runs and across a source rebuild (they never depend on a UC
object id, which is not stable across ``recreate.py``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# Definition keys that are governance (tracked by governance_fingerprint), so they
# are excluded from the DDL fingerprint even though inventory stores them inline.
_GOVERNANCE_DEFINITION_KEYS = ("column_tags",)


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ddl_fingerprint(row: Mapping[str, Any]) -> str:
    """Structural-DDL hash of an inventory row (governance tags excluded).

    Inline classic masks / row filters live in the CREATE DDL, so they ARE part of
    this fingerprint — a classic-mask change reads as a DDL change (a table then
    reports ``CHANGED``, since altering it can affect data). Object/column tags are
    excluded (they are the governance fingerprint's job).
    """
    definition = dict(row.get("definition") or {})
    for key in _GOVERNANCE_DEFINITION_KEYS:
        definition.pop(key, None)
    payload = {
        "object_type": row.get("object_type"),
        "definition": definition,
        "owner": row.get("owner"),
    }
    return _sha(payload)


def governance_fingerprint(row: Mapping[str, Any]) -> str:
    """Hash of governance applied post-create: object-level + per-column tags.

    A brand-new ABAC policy is a *separate* object (its own row + ddl_fingerprint),
    so policy creation is carried by that object's delta, not by this hash — this is
    only the per-securable tag state that ``ALTER … SET TAGS`` re-applies.
    """
    definition = row.get("definition") or {}
    payload = {
        "tags": dict(row.get("tags") or {}),
        "column_tags": definition.get("column_tags") or {},
    }
    return _sha(payload)


def grant_fingerprint_set(row: Mapping[str, Any]) -> dict[str, list[str]]:
    """Normalised explicit-grant set ``{principal: sorted(privileges)}``.

    Only explicit grants (what the permissions API returns directly on the
    securable) are represented; inherited privileges are never included. The
    ``__PERMISSIONS_UNAVAILABLE__`` sentinel (a read error placeholder) is dropped so
    a transient read failure never reads as a grant change.
    """
    out: dict[str, list[str]] = {}
    for grant in row.get("grants") or []:
        if not isinstance(grant, Mapping):
            continue
        principal = str(grant.get("principal") or "").strip()
        if not principal or principal == "__PERMISSIONS_UNAVAILABLE__":
            continue
        privileges = sorted({str(p) for p in (grant.get("privileges") or [])})
        # A principal can appear more than once (e.g. owner pseudo-grant); union.
        merged = sorted(set(out.get(principal, [])) | set(privileges))
        out[principal] = merged
    return out


def grant_fingerprint(row: Mapping[str, Any]) -> str:
    """Stable hash of the explicit-grant set (for a quick equality check)."""
    return _sha(grant_fingerprint_set(row))
