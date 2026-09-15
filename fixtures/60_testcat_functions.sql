-- ai27_ucsync_testcatalog.functions — UDFs (created FIRST so masks/policies resolve).
-- SQL scalar masks (admin bypass), SQL boolean row-filter predicates, a Python UDF, and
-- a SQL table-valued function. Ride into migration via information_schema.routines.

-- ---- SQL scalar column-mask UDFs (admins see cleartext) ----
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_ssn(v STRING)
  RETURNS STRING COMMENT 'Mask SSN except last 4'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE 'XXX-XX-' || right(v, 4) END;

CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_email(v STRING)
  RETURNS STRING COMMENT 'Mask local-part of email'
  RETURN CASE WHEN is_account_group_member('admins') THEN v
              ELSE regexp_replace(v, '(^[^@]).*(@.*$)', '$1***$2') END;

CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_account(v STRING)
  RETURNS STRING COMMENT 'Mask account/number except last 4'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('****', right(v, 4)) END;

CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.mask_phone(v STRING)
  RETURNS STRING COMMENT 'Mask phone except last 4'
  RETURN CASE WHEN is_account_group_member('admins') THEN v ELSE concat('***-***-', right(v, 4)) END;

-- ---- SQL boolean row-filter predicate UDFs ----
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.dept_filter(dept STRING)
  RETURNS BOOLEAN COMMENT 'Row filter: admins or PUBLIC dept'
  RETURN is_account_group_member('admins') OR dept = 'PUBLIC';

CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.region_filter(region STRING)
  RETURNS BOOLEAN COMMENT 'Row filter: admins or US region'
  RETURN is_account_group_member('admins') OR region = 'US';

-- ---- Python UDF (LANGUAGE PYTHON) — extra function-language coverage ----
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.py_normalize(s STRING)
  RETURNS STRING COMMENT 'Python UDF: trim + lowercase'
  LANGUAGE PYTHON
  AS $$
return (s or '').strip().lower()
$$;

-- ---- SQL table-valued function (RETURNS TABLE) — TVF coverage, self-contained ----
CREATE OR REPLACE FUNCTION ai27_ucsync_testcatalog.functions.tvf_seq(n INT)
  RETURNS TABLE(seq INT, label STRING) COMMENT 'TVF example: n synthetic rows'
  RETURN SELECT CAST(x AS INT) AS seq, concat('row_', x) AS label
         FROM (SELECT explode(sequence(1, n)) AS x);
