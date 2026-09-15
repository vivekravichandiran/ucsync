-- ai27_ucsync_testcatalog.advanced.emp_metrics — metric (semantic) view.
-- A MIGRATED object (the tool replays the YAML metric-view definition). Lives in the
-- advanced schema alongside the report-only zoo but is genuinely migrated.

CREATE OR REPLACE VIEW ai27_ucsync_testcatalog.advanced.emp_metrics
  WITH METRICS
  LANGUAGE YAML
  COMMENT 'Metric view over core_tables.employees'
  AS $$
version: 0.1
source: ai27_ucsync_testcatalog.core_tables.employees
dimensions:
  - name: department
    expr: dept
measures:
  - name: headcount
    expr: count(1)
  - name: avg_salary
    expr: avg(salary)
$$;
