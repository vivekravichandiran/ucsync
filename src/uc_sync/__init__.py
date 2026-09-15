"""Unity Catalog Sync framework (Databricks-native)."""

import os as _os


def _read_version() -> str:
    """Single source of truth for the utility version: the root ``VERSION`` file (SemVer),
    with a safe literal fallback. Stamped into ``manifest.json``, ``export_index.json``,
    ``uc_sync_audit`` and ``uc_sync_state``, so every artifact records exactly which build
    produced it. Bump by editing ``VERSION`` only (and ``pyproject.toml`` to match)."""
    try:
        root = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), _os.pardir, _os.pardir)
        with open(_os.path.join(root, "VERSION"), "r", encoding="utf-8") as fh:
            v = fh.read().strip()
            if v:
                return v
    except Exception:
        pass
    return "1.0.0"


__version__ = _read_version()
