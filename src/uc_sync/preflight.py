"""Graded environment preflight (task 9).

Run at the top of 01/02/03 so a run **never silently half-completes or completes
without its report** — the failure mode where a proxy-restricted cluster is missing
``openpyxl`` and the report generation is quietly swallowed. Mirrors the Workspace
Migration Utility's graded preflight: it grades **environment** conditions only and
returns ``GO`` / ``GO-WITH-WARNINGS`` / ``NO-GO``.

* **BLOCKING (→ NO-GO):** required libraries importable at the right versions;
  the SQL warehouse reachable; the scoped catalogs readable.
* **DEGRADING (→ GO-WITH-WARNINGS):** e.g. the ops state schema absent on a first
  run (it will be created).
* **COSMETIC:** informational only.

External-object placement is **not** a preflight concern (task 3) — the preflight
checks the environment, never whether each external object's path will succeed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Minimum library versions for the DBR 15.4 closure (a minimal proxy allowlist:
# databricks-sdk + openpyxl + et-xmlfile, no protobuf pull). Kept in lockstep with
# requirements.txt / pyproject.toml / jobs/*.json.
_REQUIRED_LIBS = [
    ("openpyxl", (3, 1), "report generation (xlsx)", "openpyxl"),
    ("PyYAML", (6, 0), "config file parsing", "yaml"),
    ("databricks-sdk", (0, 36), "Jobs API + workspace client", "databricks.sdk"),
]

BLOCKING = "BLOCKING"
DEGRADING = "DEGRADING"
COSMETIC = "COSMETIC"

GO = "GO"
GO_WITH_WARNINGS = "GO-WITH-WARNINGS"
NO_GO = "NO-GO"


class PreflightError(RuntimeError):
    """Raised when an enforced preflight returns NO-GO."""


@dataclass
class Check:
    name: str
    severity: str
    passed: bool
    message: str = ""


@dataclass
class PreflightResult:
    checks: list[Check] = field(default_factory=list)

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity == BLOCKING and not c.passed]

    @property
    def degrading_failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity == DEGRADING and not c.passed]

    @property
    def verdict(self) -> str:
        if self.blocking_failures:
            return NO_GO
        if self.degrading_failures:
            return GO_WITH_WARNINGS
        return GO

    def render(self) -> str:
        # Lead with the PASS/FAIL result (the severity is a parenthetical), so a
        # passing blocking check reads "PASS (blocking): …" rather than the
        # alarming-looking "[BLOCKING] ok".
        lines = [f"UC Sync preflight: {self.verdict}"]
        for c in self.checks:
            mark = "PASS" if c.passed else "FAIL"
            lines.append(f"  {mark} ({c.severity.lower()}): {c.name}"
                         + (f" — {c.message}" if c.message else ""))
        return "\n".join(lines)


def _installed_version(package: str) -> Optional[tuple[int, ...]]:
    try:
        from importlib.metadata import version
    except ImportError:  # pragma: no cover - Py<3.8
        return None
    try:
        raw = version(package)
    except Exception:  # noqa: BLE001 - not installed / unknown
        return None
    parts: list[int] = []
    for token in str(raw).split(".")[:3]:
        num = ""
        for ch in token:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


def check_libraries() -> list[Check]:
    """One BLOCKING check per required library: importable at the right version,
    with an actionable message on failure (never a silent skip)."""
    checks: list[Check] = []
    for entry in _REQUIRED_LIBS:
        package, min_version, purpose = entry[0], entry[1], entry[2]
        import_name = entry[3] if len(entry) > 3 else package.replace("-", "_")
        found = _installed_version(package)
        importable = True
        try:
            __import__(import_name)
        except Exception:  # noqa: BLE001
            importable = False
        min_str = ".".join(str(p) for p in min_version)
        if not importable or found is None:
            checks.append(Check(
                name=f"library {package}>={min_str}",
                severity=BLOCKING, passed=False,
                message=(f"not importable ({purpose}); install "
                         f"{package}>={min_str} on the cluster / job libraries"),
            ))
            continue
        ok = found >= min_version
        checks.append(Check(
            name=f"library {package}>={min_str}",
            severity=BLOCKING, passed=ok,
            message=("" if ok else
                     f"found {'.'.join(str(p) for p in found)}, need >= {min_str} "
                     f"({purpose})"),
        ))
    return checks


def run_preflight(
    *,
    check_libs: bool = True,
    warehouse_reachable: Optional[bool] = None,
    catalogs_readable: Optional[bool] = None,
    ops_state_present: Optional[bool] = None,
) -> PreflightResult:
    """Grade the environment. ``None`` for a probe means "not applicable / not
    probed" (skipped, not a failure). A ``False`` warehouse/catalog probe is
    BLOCKING; a ``False`` ops-state probe is DEGRADING (it will be created)."""
    checks: list[Check] = []
    if check_libs:
        checks.extend(check_libraries())
    if warehouse_reachable is not None:
        checks.append(Check(
            "SQL warehouse reachable", BLOCKING, bool(warehouse_reachable),
            "" if warehouse_reachable else
            "the configured SQL warehouse did not respond — check the id / that it "
            "is running and the run principal has CAN USE",
        ))
    if catalogs_readable is not None:
        checks.append(Check(
            "scoped catalogs readable", BLOCKING, bool(catalogs_readable),
            "" if catalogs_readable else
            "a scoped catalog could not be read — check the catalog name(s) and the "
            "run principal's USE CATALOG grant",
        ))
    if ops_state_present is not None:
        checks.append(Check(
            "ops state schema present", DEGRADING, bool(ops_state_present),
            "" if ops_state_present else
            "ops state schema absent — it will be created on first run",
        ))
    return PreflightResult(checks=checks)


def enforce_preflight(result: PreflightResult, *, enforce: bool = True) -> PreflightResult:
    """Print the graded result. When ``enforce`` (default) and the verdict is NO-GO,
    raise ``PreflightError`` with an actionable message (a red run, never a silent
    degrade). When ``enforce`` is off, a NO-GO downgrades to a loud warning."""
    print(result.render())
    if result.verdict == NO_GO:
        detail = "; ".join(
            f"{c.name}: {c.message}" for c in result.blocking_failures
        )
        if enforce:
            raise PreflightError(
                "Preflight NO-GO (preflight_enforce=true): " + detail
            )
        print("[preflight] WARNING: NO-GO but preflight_enforce=false — continuing "
              "despite: " + detail)
    return result
