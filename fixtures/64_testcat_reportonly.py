#!/usr/bin/env python3
"""Report-only UC objects for ai27_ucsync_testcatalog.advanced (SOURCE workspace).

These are INVENTORIED but not migrated by UC Sync — they exist so the tool reports
them. Reproduces the real objects (no simulations). Idempotent: safe to re-run.

  1. Materialized view  mv_emp_by_dept          (CREATE MATERIALIZED VIEW on the warehouse)
  2. Streaming table    st_events  + event log  sdp_event_log   (real Lakeflow pipeline)
  3. Quality monitor    on core_tables.employees (snapshot; SDK)
  4. Registered model   emp_churn_model + 2 versions (notebook job on an ML-runtime cluster)
  5. Vector Search      endpoint ai27-ucsync-vs + index emp_vs_index (source advanced.emp_docs)
  6. Lakebase           instance ai27-ucsync-lakebase + synced table cust_synced

Config comes from environment (config.env, exported by recreate.py). Run via
`python3 fixtures/recreate.py tc_reportonly` (or directly with config.env sourced).

Note: the metric view advanced.emp_metrics is a MIGRATED object and is created by
67_testcat_metric_view.sql (stage tc_metric), not here.
"""
import json, os, subprocess, sys, time

P     = os.environ.get("SRC_PROFILE", "source_ws")
WH    = os.environ.get("SRC_WAREHOUSE", "d17c387e12f4f010")
CAT   = os.environ.get("TESTCAT_CATALOG", "ai27_ucsync_testcatalog")
SCH   = "advanced"
USER  = os.environ.get("ADMIN", "abhishek.iyer@databricks.com")
VS_ENDPOINT = "ai27-ucsync-vs"
LB_INSTANCE = "ai27-ucsync-lakebase"
NB_PIPELINE = f"/Users/{USER}/ucsync_testcat_pipeline"
NB_MODEL    = f"/Users/{USER}/ucsync_testcat_model"


def sql(stmt):
    body = {"warehouse_id": WH, "statement": stmt, "wait_timeout": "50s"}
    p = subprocess.run(["databricks", "api", "post", "/api/2.0/sql/statements",
                        "-p", P, "--json", json.dumps(body)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return "CLI_ERROR", p.stderr[:300]
    out = json.loads(p.stdout); sid = out.get("statement_id")
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING") and sid:
        time.sleep(2)
        g = subprocess.run(["databricks", "api", "get",
                            f"/api/2.0/sql/statements/{sid}", "-p", P],
                           capture_output=True, text=True)
        out = json.loads(g.stdout)
    st = out.get("status", {})
    return st.get("state"), st.get("error", {}).get("message", "")


def cli(args, inp=None):
    return subprocess.run(["databricks", *args, "-p", P],
                          capture_output=True, text=True, input=inp)


def ws_import(path, language, local_text):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".src", delete=False) as f:
        f.write(local_text); tmp = f.name
    cli(["workspace", "import", path, "--language", language,
         "--format", "SOURCE", "--file", tmp, "--overwrite"])
    os.unlink(tmp)


# ---- 1. Materialized view ----
def materialized_view():
    state, err = sql(f"""CREATE MATERIALIZED VIEW IF NOT EXISTS {CAT}.{SCH}.mv_emp_by_dept
      COMMENT 'MV: headcount + avg salary by dept'
      AS SELECT dept, count(1) AS headcount, avg(salary) AS avg_salary
         FROM {CAT}.core_tables.employees GROUP BY dept""")
    print(f"MV mv_emp_by_dept: {state} {err[:120]}")


# ---- 2. Streaming table + event log via a real Lakeflow pipeline ----
def pipeline():
    ws_import(NB_PIPELINE, "SQL",
              "-- Databricks notebook source\n"
              f"CREATE OR REFRESH STREAMING TABLE st_events\n"
              f"  COMMENT 'Streaming table (Lakeflow SDP) over orders_clustered'\n"
              f"  AS SELECT order_id, region, amount, order_ts\n"
              f"     FROM STREAM({CAT}.core_tables.orders_clustered);\n")
    # find existing pipeline by name
    lst = cli(["pipelines", "list-pipelines", "--output", "json"])
    pid = ""
    try:
        for pl in (json.loads(lst.stdout) or []):
            if pl.get("name") == "ai27_ucsync_testcat_pipeline":
                pid = pl.get("pipeline_id"); break
    except Exception:
        pass
    if not pid:
        spec = {"name": "ai27_ucsync_testcat_pipeline", "serverless": True,
                "catalog": CAT, "schema": SCH,
                "event_log": {"catalog": CAT, "schema": SCH, "name": "sdp_event_log"},
                "libraries": [{"notebook": {"path": NB_PIPELINE}}],
                "development": False, "continuous": False, "channel": "CURRENT"}
        r = cli(["pipelines", "create", "--json", json.dumps(spec)])
        try:
            pid = json.loads(r.stdout).get("pipeline_id", "")
        except Exception:
            print(f"PIPELINE create: {r.stderr[:200]}"); return
    cli(["pipelines", "start-update", pid])
    for _ in range(30):
        g = cli(["pipelines", "get", pid, "--output", "json"])
        try:
            d = json.loads(g.stdout)
            state = d.get("latest_updates", [{}])[0].get("state", "?")
        except Exception:
            state = "?"
        if "COMPLETED" in state:
            print(f"PIPELINE st_events + sdp_event_log: COMPLETED ({pid})"); return
        if "FAILED" in state or "CANCELED" in state:
            print(f"PIPELINE: {state} ({pid})"); return
        time.sleep(20)
    print(f"PIPELINE: still running ({pid})")


# ---- 3. Quality monitor ----
def monitor():
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.catalog import MonitorSnapshot
        w = WorkspaceClient(profile=P)
        tbl = f"{CAT}.core_tables.employees"
        try:
            w.quality_monitors.get(table_name=tbl); print("MONITOR: EXISTS"); return
        except Exception:
            pass
        w.quality_monitors.create(table_name=tbl, snapshot=MonitorSnapshot(),
                                  assets_dir=f"/Workspace/Users/{USER}/ucsync_testcat_monitor",
                                  output_schema_name=f"{CAT}.{SCH}")
        print("MONITOR: CREATED")
    except Exception as e:
        print(f"MONITOR: BLOCKED {repr(e)[:200]}")


# ---- 4. Registered model + 2 versions (ML-runtime notebook job; mlflow preinstalled) ----
def model():
    name = f"{CAT}.{SCH}.emp_churn_model"
    g = cli(["registered-models", "get", name])
    if g.returncode == 0 and name in g.stdout:
        print("MODEL: EXISTS"); return
    ws_import(NB_MODEL, "PYTHON",
              "# Databricks notebook source\n"
              "import mlflow, mlflow.sklearn, numpy as np\n"
              "from sklearn.linear_model import LogisticRegression\n"
              "from mlflow.models.signature import infer_signature\n"
              "mlflow.set_registry_uri('databricks-uc')\n"
              f"name = '{name}'\n"
              "X = np.array([[0.0],[1.0],[2.0],[3.0]]); y = np.array([0,0,1,1])\n"
              "for i in range(2):\n"
              "    with mlflow.start_run(run_name=f'v{i+1}'):\n"
              "        m = LogisticRegression().fit(X, y)\n"
              "        sig = infer_signature(X, m.predict(X))\n"
              "        mlflow.sklearn.log_model(m, artifact_path='model', registered_model_name=name, signature=sig)\n"
              "print('registered 2 versions')\n")
    spec = {"run_name": "ucsync_testcat_model",
            "tasks": [{"task_key": "m",
                       "notebook_task": {"notebook_path": NB_MODEL},
                       "new_cluster": {"spark_version": "15.4.x-cpu-ml-scala2.12",
                                       "node_type_id": "Standard_DS3_v2", "num_workers": 0,
                                       "data_security_mode": "SINGLE_USER", "single_user_name": USER,
                                       "spark_conf": {"spark.databricks.cluster.profile": "singleNode",
                                                      "spark.master": "local[*]"},
                                       "custom_tags": {"ResourceClass": "SingleNode"}}}]}
    r = cli(["jobs", "submit", "--json", json.dumps(spec)])
    print(f"MODEL: {'CREATED (2 versions)' if r.returncode == 0 else 'FAILED ' + r.stderr[:200]}")


# ---- 5. Vector Search endpoint + index ----
def vector_search():
    # endpoint
    g = cli(["vector-search-endpoints", "get-endpoint", VS_ENDPOINT])
    if g.returncode != 0:
        cli(["vector-search-endpoints", "create-endpoint", VS_ENDPOINT, "STANDARD"])
        for _ in range(30):
            gg = cli(["vector-search-endpoints", "get-endpoint", VS_ENDPOINT, "--output", "json"])
            try:
                if json.loads(gg.stdout).get("endpoint_status", {}).get("state") == "ONLINE":
                    break
            except Exception:
                pass
            time.sleep(20)
    # source table (CDF + self-managed embeddings)
    sql(f"""CREATE TABLE IF NOT EXISTS {CAT}.{SCH}.emp_docs
        (id INT NOT NULL, text STRING, embedding ARRAY<FLOAT>) USING DELTA
        TBLPROPERTIES ('delta.enableChangeDataFeed'='true','ai27_uc.fixture'='true')""")
    sql(f"""INSERT INTO {CAT}.{SCH}.emp_docs SELECT * FROM (SELECT 1 id, 'engineering role' text, array(0.1,0.2,0.3) embedding
        UNION ALL SELECT 2,'sales role',array(0.4,0.5,0.6) UNION ALL SELECT 3,'finance role',array(0.7,0.8,0.9))
        WHERE (SELECT count(1) FROM {CAT}.{SCH}.emp_docs)=0""")
    # index
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.vectorsearch import (
            DeltaSyncVectorIndexSpecRequest, EmbeddingVectorColumn, VectorIndexType, PipelineType)
        w = WorkspaceClient(profile=P)
        idx = f"{CAT}.{SCH}.emp_vs_index"
        try:
            w.vector_search_indexes.get_index(index_name=idx); print("VS INDEX: EXISTS"); return
        except Exception:
            pass
        w.vector_search_indexes.create_index(
            name=idx, endpoint_name=VS_ENDPOINT, primary_key="id",
            index_type=VectorIndexType.DELTA_SYNC,
            delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                source_table=f"{CAT}.{SCH}.emp_docs", pipeline_type=PipelineType.TRIGGERED,
                embedding_vector_columns=[EmbeddingVectorColumn(name="embedding", embedding_dimension=3)]))
        print("VS INDEX: CREATED")
    except Exception as e:
        print(f"VS INDEX: BLOCKED {repr(e)[:200]}")


# ---- 6. Lakebase instance + synced table ----
def lakebase():
    g = cli(["database", "get-database-instance", LB_INSTANCE])
    if g.returncode != 0:
        cli(["database", "create-database-instance", "--json",
             json.dumps({"name": LB_INSTANCE, "capacity": "CU_1"})])
    for _ in range(30):
        gg = cli(["database", "get-database-instance", LB_INSTANCE, "--output", "json"])
        try:
            if json.loads(gg.stdout).get("state") == "AVAILABLE":
                break
        except Exception:
            pass
        time.sleep(15)
    # synced table — source must NOT have masks/row filters (departments is clean)
    spec = {"name": f"{CAT}.{SCH}.cust_synced", "database_instance_name": LB_INSTANCE,
            "logical_database_name": "databricks_postgres",
            "spec": {"source_table_full_name": f"{CAT}.core_tables.departments",
                     "primary_key_columns": ["dept_id"], "scheduling_policy": "SNAPSHOT",
                     "create_database_objects_if_missing": True}}
    r = cli(["database", "create-synced-database-table", "--json", json.dumps(spec)])
    ok = r.returncode == 0 or "already exists" in (r.stderr or "").lower()
    print(f"LAKEBASE cust_synced: {'CREATED/EXISTS' if ok else 'BLOCKED ' + r.stderr[:200]}")


if __name__ == "__main__":
    materialized_view()
    pipeline()
    monitor()
    model()
    vector_search()
    lakebase()
    print("=== report-only infra stage done ===")
