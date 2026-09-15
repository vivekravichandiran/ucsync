# repro_view_403.py
# ---------------------------------------------------------------------------
# Reproduce the IMPORT-time HTTP 403 on the view
# `<target>.data_security.pii_classification_anlysis_vw`.
#
# WHAT THE REPORTS ALREADY PROVED (2026-09-11, read directly):
#   - export.xlsx › Views: export_status = EXPORTED  -> the DDL WAS captured.
#     The "export never created the view" theory is DISPROVEN.
#   - import.xlsx › Views: import_status = FAILED ... submit failed: HTTP 403:
#     {"error_code":403,"message":"Invalid request. [ReqId: 7ec3bf53-...]"}
#     -> this is an IMPORT failure at CREATE OR REPLACE VIEW, and the body is an
#        edge/gateway-shaped 403 (numeric error_code + "Invalid request." + ReqId),
#        NOT the SQL app-layer envelope (which uses string codes like PERMISSION_DENIED).
#
# WHAT WE ISOLATED (from notebooks/03_Import.py + package_import.py):
#   - Tables / masks / functions run on SparkSqlExecutor (spark.sql on the cluster)
#     -> 701 tables, 1337 masks, 73 functions all CREATED. Those never touch HTTP.
#   - Views (+ ABAC) run on RestSqlExecutor(wc, import_warehouse_id), where
#     wc = WorkspaceClient(local_workspace_auth(dbutils)) -> the NOTEBOOK CONTEXT
#     TOKEN (context.apiToken()) against the TARGET host. The view was the ONLY
#     statement in the whole import to go over the REST Statement Execution API.
#   - The REST code is fine: export made ~892 successful REST POSTs, but with the
#     SOURCE workspace client + an explicit source-SPN secret. The import call
#     differs ONLY in the client/auth (local notebook-context token) + host + warehouse.
#   - Option C (a job, run-as SPN, SELECT 1 on the import warehouse) SUCCEEDED, so
#     the SPN HAS CAN USE on the warehouse. But Option C ran SQL the normal way — it
#     never used the utility's urllib + notebook-token (+ proxy) REST path.
#
# THIS SCRIPT closes the gap: it makes the EXACT import call — CREATE OR REPLACE VIEW
# POSTed to import_warehouse_id, over urllib, with the notebook context token — i.e.
# the identical `local_workspace_auth` mechanism the utility uses at import.
#
# HOW TO RUN (as agreed):
#   Create a job in the TARGET workspace (mobility-stg), set "Run as" = the import SPN
#   (the one with CAN USE on this warehouse and USE on this catalog), point it at this
#   notebook, set the two variables below, and run. Running as the SPN (not your PAT)
#   is what makes this faithful.
# ---------------------------------------------------------------------------

import os
import json
import urllib.request
import urllib.error

# --- Auth: the SAME mechanism as uc_sync.auth.local_workspace_auth ----------
# (auth.py: host = context.apiUrl().get(); token = context.apiToken().get())
_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
host = _ctx.apiUrl().get().rstrip("/")
token = _ctx.apiToken().get()

# --- TODO: set these two ----------------------------------------------------
IMPORT_WAREHOUSE_ID = "PUT_THE_IMPORT_WAREHOUSE_ID_HERE"   # cfg.import_warehouse_id (the view/ABAC warehouse)
SOURCE_CATALOG = "jio_mobility_prod"                        # bundle was captured under this
TARGET_CATALOG = "jio_mobility_ucmig"                       # the dummy/target catalog you imported into

# Confirm WHO this is running as — must be the import SPN, not your user.
try:
    who = spark.sql("SELECT current_user()").collect()[0][0]
except Exception as _e:  # noqa: BLE001
    who = f"(current_user() unavailable: {_e!r})"
print("running as        :", who)
print("host (apiUrl)     :", host)
print("import warehouse  :", IMPORT_WAREHOUSE_ID)
print("proxy env         :", {k: os.environ.get(k) for k in
      ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy")})


# ---------------------------------------------------------------------------
# The statement the utility replays at import:
#   - view NAME qualified to the TARGET catalog
#   - CREATE OR REPLACE VIEW (views are normalized to OR REPLACE)
#   - body catalog refs rewritten SOURCE -> TARGET (what _CatalogRewritingExecutor does)
# The captured (source) body, verbatim, with the catalog swap applied at the end.
# ---------------------------------------------------------------------------
create_view_source = r"""CREATE OR REPLACE VIEW jio_mobility_prod.data_security.pii_classification_anlysis_vw (
  catalog_name,
  schema_name,
  table_name,
  column_name,
  has_pii,
  classified_pii_tag,
  current_masking_function,
  proposed_masking_function,
  current_masking_function_simplified,
  proposed_masking_function_simplified,
  raw_explanation,
  run_id,
  status)
WITH SCHEMA COMPENSATION
AS with analysis as (
select
catalog_name, schema_name, table_name, column_name, has_pii,
 nullif(classified_pii_tag, 'null') as classified_pii_tag,
nullif(current_mask_name_alt, 'null') as current_masking_function,
nullif(proposed_masking_function,'null') as proposed_masking_function,
regexp_replace(nullif(current_mask_name_alt, 'null'), '_(string|double|bigint|int|boolean|float|decimal|date|timestamp|binary|short|long|byte)$', '') as current_masking_function_simplified,
regexp_replace(nullif(proposed_masking_function, 'null'), '_(string|double|bigint|int|boolean|float|decimal|date|timestamp|binary|short|long|byte)$', '') as proposed_masking_function_simplified,
raw_explanation,
run_id

from (
    select
  t1.*,
  t2.mask_name as current_mask_name_alt
  from jio_mobility_prod.data_security.pii_classification_results t1
  left outer join jio_mobility_prod.information_schema.column_masks t2
  on (t1.catalog_name = t2.table_catalog and t1.schema_name = t2.table_schema and t1.table_name = t2.table_name and t1.column_name = t2.column_name and t1.run_id = 'eabdaa1c-4c95-425c-bbc1-96225ba96331')

)
--where nvl(error_message,'null')!= 'null'
where nvl(proposed_masking_function,'null')!='null' or nvl(current_mask_name_alt,'null')!='null'
),
analyzed_status as (
select * ,
case
when current_masking_function_simplified is null then 'OVERCLASSIFIED'
when proposed_masking_function_simplified is null then 'UNDERCLASSIFIED'
when current_masking_function_simplified = proposed_masking_function_simplified then 'MATCH'
else 'MISMATCH' end as status
from analysis
)
select * from analyzed_status
"""

# Catalog rewrite source -> target (mirrors _CatalogRewritingExecutor).
create_view = create_view_source.replace(SOURCE_CATALOG, TARGET_CATALOG)


def submit_like_the_utility(statement, warehouse_id):
    """POST exactly like RestSqlExecutor._run_once via WorkspaceClient.request:
    urllib, Bearer <notebook context token>, /api/2.0/sql/statements, same body."""
    url = host + "/api/2.0/sql/statements"
    body = {
        "warehouse_id": warehouse_id,
        "statement": statement,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
        "disposition": "INLINE",
        "format": "JSON_ARRAY",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    print("=" * 90)
    print(f"POST {url}  (warehouse={warehouse_id})")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            xrid = resp.headers.get("x-request-id")
            raw = resp.read().decode() or "{}"
        print("HTTP status   :", status)     # 200 here = the POST was ACCEPTED (submit OK)
        print("x-request-id  :", xrid)
        payload = json.loads(raw) if raw.strip() else {}
        state = str((payload.get("status") or {}).get("state") or "").upper()
        print("stmt state    :", state)      # SUCCEEDED / FAILED / PENDING / RUNNING
        if state in {"FAILED", "CANCELED", "CLOSED"}:
            err = (payload.get("status") or {}).get("error") or {}
            print("stmt error    :", err.get("error_code"), "|", err.get("message"))
        print("body[:2000]   :", raw[:2000])
    except urllib.error.HTTPError as exc:
        # THIS branch = the submit POST itself was rejected (the run's failure mode:
        # "submit failed: HTTP 403"). This is what we're trying to reproduce.
        detail = exc.read().decode() if hasattr(exc, "read") else str(exc)
        print("HTTP status   :", exc.code, "  <-- submit REJECTED (the reproduced failure)")
        print("x-request-id  :", exc.headers.get("x-request-id") if exc.headers else None)
        print("body          :", detail[:2000])
    except Exception as exc:  # noqa: BLE001
        print("NON-HTTP error:", repr(exc))
    print("=" * 90)


submit_like_the_utility(create_view, IMPORT_WAREHOUSE_ID)

# ---------------------------------------------------------------------------
# INTERPRETATION
# - HTTP status 403, body {"error_code":403,"message":"Invalid request. [ReqId:...]"}
#       -> REPRODUCED. The failure is the utility's import REST path (notebook-context
#          token + urllib + proxy against the target host), NOT permissions / NOT the
#          SQL / NOT export. Fixes to weigh: (1) auth the import REST executor with the
#          SAME explicit-SPN secret that export uses (not the notebook context token);
#          (2) ensure the target host bypasses / is allowed by the forward proxy
#          (NO_PROXY); (3) fail fast on 4xx instead of retrying 5x (import_engine.py).
# - HTTP status 200 + stmt state FAILED (e.g. TABLE_OR_VIEW_NOT_FOUND / parse):
#       -> the edge ACCEPTED the request; the 403 was environmental/transient at the
#          original run moment. The statement content itself is then the only remaining
#          question (and a not-found here just means the target tables aren't present in
#          this dummy catalog — not the migration failure).
# - HTTP status 200 + stmt state SUCCEEDED:
#       -> fully works now; the original 403 was transient. Capture cluster/proxy state.
# ---------------------------------------------------------------------------
