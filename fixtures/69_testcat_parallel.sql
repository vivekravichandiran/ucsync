-- ai27_ucsync_testcatalog — PARALLELISM STRESS BED (backlog item 3).
-- 120 plain tables across 3 schemas (40 each), 5-10 rows each. Deliberately
-- many same-rank objects so the within-level import pool (parallel_threads)
-- and the export SHOW CREATE pre-capture pool are exercised under load.
-- Plain tables only (no governance) — the stress is throughput, not policy.

CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a COMMENT 'parallelism stress-test schema';
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_000 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_000 VALUES
  (1,'parallel_a_t000_r1',14692.03,DATE'2024-12-09'),
  (2,'parallel_a_t000_r2',32198.28,DATE'2024-03-24'),
  (3,'parallel_a_t000_r3',13534.86,DATE'2024-12-18'),
  (4,'parallel_a_t000_r4',11495.75,DATE'2024-07-02'),
  (5,'parallel_a_t000_r5',4005.11,DATE'2024-04-08'),
  (6,'parallel_a_t000_r6',66337.77,DATE'2024-01-18'),
  (7,'parallel_a_t000_r7',26162.91,DATE'2024-11-23'),
  (8,'parallel_a_t000_r8',71526.53,DATE'2024-04-15'),
  (9,'parallel_a_t000_r9',77336.35,DATE'2024-01-25'),
  (10,'parallel_a_t000_r10',21026.89,DATE'2024-07-11');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_001 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_001 VALUES
  (1,'parallel_a_t001_r1',20479.27,DATE'2024-06-04'),
  (2,'parallel_a_t001_r2',12256.48,DATE'2024-02-12'),
  (3,'parallel_a_t001_r3',45182.77,DATE'2024-05-26'),
  (4,'parallel_a_t001_r4',5795.93,DATE'2024-08-18'),
  (5,'parallel_a_t001_r5',16461.48,DATE'2024-02-18'),
  (6,'parallel_a_t001_r6',38527.80,DATE'2024-10-28'),
  (7,'parallel_a_t001_r7',47500.73,DATE'2024-04-23');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_002 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_002 VALUES
  (1,'parallel_a_t002_r1',6106.84,DATE'2024-04-25'),
  (2,'parallel_a_t002_r2',38030.10,DATE'2024-04-28'),
  (3,'parallel_a_t002_r3',13338.48,DATE'2024-05-15'),
  (4,'parallel_a_t002_r4',83420.46,DATE'2024-03-12'),
  (5,'parallel_a_t002_r5',46666.26,DATE'2024-11-09');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_003 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_003 VALUES
  (1,'parallel_a_t003_r1',89693.82,DATE'2024-02-20'),
  (2,'parallel_a_t003_r2',83327.21,DATE'2024-09-24'),
  (3,'parallel_a_t003_r3',32187.20,DATE'2024-08-13'),
  (4,'parallel_a_t003_r4',35482.81,DATE'2024-12-18'),
  (5,'parallel_a_t003_r5',28885.87,DATE'2024-06-27'),
  (6,'parallel_a_t003_r6',7431.29,DATE'2024-01-26'),
  (7,'parallel_a_t003_r7',41447.51,DATE'2024-05-03'),
  (8,'parallel_a_t003_r8',27753.72,DATE'2024-12-11'),
  (9,'parallel_a_t003_r9',27969.83,DATE'2024-08-13'),
  (10,'parallel_a_t003_r10',84359.58,DATE'2024-03-09');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_004 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_004 VALUES
  (1,'parallel_a_t004_r1',32425.95,DATE'2024-09-18'),
  (2,'parallel_a_t004_r2',34538.95,DATE'2024-10-14'),
  (3,'parallel_a_t004_r3',76584.51,DATE'2024-06-08'),
  (4,'parallel_a_t004_r4',18231.65,DATE'2024-08-03'),
  (5,'parallel_a_t004_r5',99161.06,DATE'2024-02-05'),
  (6,'parallel_a_t004_r6',82340.20,DATE'2024-11-14');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_005 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_005 VALUES
  (1,'parallel_a_t005_r1',8426.49,DATE'2024-07-20'),
  (2,'parallel_a_t005_r2',61448.67,DATE'2024-05-18'),
  (3,'parallel_a_t005_r3',1604.87,DATE'2024-12-04'),
  (4,'parallel_a_t005_r4',89453.68,DATE'2024-05-25'),
  (5,'parallel_a_t005_r5',84112.43,DATE'2024-02-10'),
  (6,'parallel_a_t005_r6',57085.20,DATE'2024-08-01'),
  (7,'parallel_a_t005_r7',94746.92,DATE'2024-05-17'),
  (8,'parallel_a_t005_r8',99971.22,DATE'2024-09-04'),
  (9,'parallel_a_t005_r9',82059.38,DATE'2024-11-17');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_006 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_006 VALUES
  (1,'parallel_a_t006_r1',26171.19,DATE'2024-06-25'),
  (2,'parallel_a_t006_r2',21274.69,DATE'2024-09-01'),
  (3,'parallel_a_t006_r3',78604.41,DATE'2024-08-01'),
  (4,'parallel_a_t006_r4',14762.46,DATE'2024-05-08'),
  (5,'parallel_a_t006_r5',7692.30,DATE'2024-10-03'),
  (6,'parallel_a_t006_r6',11326.93,DATE'2024-08-27'),
  (7,'parallel_a_t006_r7',9171.97,DATE'2024-09-25'),
  (8,'parallel_a_t006_r8',16583.16,DATE'2024-11-16'),
  (9,'parallel_a_t006_r9',72163.21,DATE'2024-05-17');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_007 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_007 VALUES
  (1,'parallel_a_t007_r1',55561.27,DATE'2024-09-25'),
  (2,'parallel_a_t007_r2',95773.88,DATE'2024-04-23'),
  (3,'parallel_a_t007_r3',40957.51,DATE'2024-11-21'),
  (4,'parallel_a_t007_r4',49044.56,DATE'2024-09-15'),
  (5,'parallel_a_t007_r5',15960.31,DATE'2024-04-03'),
  (6,'parallel_a_t007_r6',44413.02,DATE'2024-10-18'),
  (7,'parallel_a_t007_r7',30261.75,DATE'2024-04-01'),
  (8,'parallel_a_t007_r8',9405.90,DATE'2024-11-02'),
  (9,'parallel_a_t007_r9',30107.08,DATE'2024-01-28');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_008 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_008 VALUES
  (1,'parallel_a_t008_r1',9387.65,DATE'2024-04-09'),
  (2,'parallel_a_t008_r2',87784.62,DATE'2024-04-18'),
  (3,'parallel_a_t008_r3',17442.92,DATE'2024-10-19'),
  (4,'parallel_a_t008_r4',62053.31,DATE'2024-08-26'),
  (5,'parallel_a_t008_r5',53454.24,DATE'2024-02-04'),
  (6,'parallel_a_t008_r6',86474.55,DATE'2024-06-14'),
  (7,'parallel_a_t008_r7',53983.59,DATE'2024-12-02');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_009 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_009 VALUES
  (1,'parallel_a_t009_r1',85749.82,DATE'2024-02-02'),
  (2,'parallel_a_t009_r2',52872.93,DATE'2024-06-26'),
  (3,'parallel_a_t009_r3',14422.31,DATE'2024-04-07'),
  (4,'parallel_a_t009_r4',70392.57,DATE'2024-03-14'),
  (5,'parallel_a_t009_r5',24150.35,DATE'2024-08-08'),
  (6,'parallel_a_t009_r6',9980.56,DATE'2024-09-04'),
  (7,'parallel_a_t009_r7',6730.83,DATE'2024-09-27'),
  (8,'parallel_a_t009_r8',2034.11,DATE'2024-04-06'),
  (9,'parallel_a_t009_r9',53369.62,DATE'2024-08-07'),
  (10,'parallel_a_t009_r10',52665.07,DATE'2024-03-13');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_010 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_010 VALUES
  (1,'parallel_a_t010_r1',51273.33,DATE'2024-08-10'),
  (2,'parallel_a_t010_r2',55544.89,DATE'2024-12-26'),
  (3,'parallel_a_t010_r3',72945.84,DATE'2024-12-16'),
  (4,'parallel_a_t010_r4',20389.24,DATE'2024-05-07'),
  (5,'parallel_a_t010_r5',7765.74,DATE'2024-12-18');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_011 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_011 VALUES
  (1,'parallel_a_t011_r1',98138.40,DATE'2024-01-02'),
  (2,'parallel_a_t011_r2',76669.61,DATE'2024-09-28'),
  (3,'parallel_a_t011_r3',69715.20,DATE'2024-01-17'),
  (4,'parallel_a_t011_r4',10600.23,DATE'2024-02-20'),
  (5,'parallel_a_t011_r5',9007.86,DATE'2024-04-13');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_012 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_012 VALUES
  (1,'parallel_a_t012_r1',74768.31,DATE'2024-10-20'),
  (2,'parallel_a_t012_r2',5309.79,DATE'2024-02-14'),
  (3,'parallel_a_t012_r3',86263.74,DATE'2024-10-17'),
  (4,'parallel_a_t012_r4',41567.33,DATE'2024-04-22'),
  (5,'parallel_a_t012_r5',93972.40,DATE'2024-04-09');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_013 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_013 VALUES
  (1,'parallel_a_t013_r1',17254.85,DATE'2024-11-10'),
  (2,'parallel_a_t013_r2',60029.40,DATE'2024-02-01'),
  (3,'parallel_a_t013_r3',60168.79,DATE'2024-10-04'),
  (4,'parallel_a_t013_r4',9702.68,DATE'2024-04-17'),
  (5,'parallel_a_t013_r5',34860.16,DATE'2024-06-03'),
  (6,'parallel_a_t013_r6',32118.47,DATE'2024-05-06'),
  (7,'parallel_a_t013_r7',57533.69,DATE'2024-12-10'),
  (8,'parallel_a_t013_r8',80273.83,DATE'2024-09-01');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_014 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_014 VALUES
  (1,'parallel_a_t014_r1',72792.38,DATE'2024-11-04'),
  (2,'parallel_a_t014_r2',17701.33,DATE'2024-02-04'),
  (3,'parallel_a_t014_r3',97410.70,DATE'2024-03-09'),
  (4,'parallel_a_t014_r4',37030.77,DATE'2024-04-23'),
  (5,'parallel_a_t014_r5',45042.26,DATE'2024-11-21'),
  (6,'parallel_a_t014_r6',34700.64,DATE'2024-08-09'),
  (7,'parallel_a_t014_r7',6758.11,DATE'2024-11-14'),
  (8,'parallel_a_t014_r8',36365.05,DATE'2024-01-11'),
  (9,'parallel_a_t014_r9',17246.81,DATE'2024-05-06'),
  (10,'parallel_a_t014_r10',97254.56,DATE'2024-09-23');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_015 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_015 VALUES
  (1,'parallel_a_t015_r1',73619.01,DATE'2024-02-03'),
  (2,'parallel_a_t015_r2',90673.19,DATE'2024-09-02'),
  (3,'parallel_a_t015_r3',48493.74,DATE'2024-09-05'),
  (4,'parallel_a_t015_r4',56433.16,DATE'2024-01-10'),
  (5,'parallel_a_t015_r5',47895.05,DATE'2024-06-07'),
  (6,'parallel_a_t015_r6',89499.31,DATE'2024-11-04'),
  (7,'parallel_a_t015_r7',46457.99,DATE'2024-09-28'),
  (8,'parallel_a_t015_r8',53364.79,DATE'2024-12-05');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_016 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_016 VALUES
  (1,'parallel_a_t016_r1',21399.22,DATE'2024-07-01'),
  (2,'parallel_a_t016_r2',23609.94,DATE'2024-06-26'),
  (3,'parallel_a_t016_r3',54064.85,DATE'2024-12-26'),
  (4,'parallel_a_t016_r4',32627.34,DATE'2024-03-26'),
  (5,'parallel_a_t016_r5',92017.13,DATE'2024-07-28'),
  (6,'parallel_a_t016_r6',5175.60,DATE'2024-04-07');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_017 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_017 VALUES
  (1,'parallel_a_t017_r1',45930.39,DATE'2024-04-08'),
  (2,'parallel_a_t017_r2',3201.84,DATE'2024-04-13'),
  (3,'parallel_a_t017_r3',43125.35,DATE'2024-02-25'),
  (4,'parallel_a_t017_r4',36685.44,DATE'2024-11-17'),
  (5,'parallel_a_t017_r5',52486.86,DATE'2024-09-11'),
  (6,'parallel_a_t017_r6',3717.14,DATE'2024-05-06'),
  (7,'parallel_a_t017_r7',76199.33,DATE'2024-01-04'),
  (8,'parallel_a_t017_r8',78293.55,DATE'2024-06-24');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_018 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_018 VALUES
  (1,'parallel_a_t018_r1',57299.77,DATE'2024-09-04'),
  (2,'parallel_a_t018_r2',50588.73,DATE'2024-04-09'),
  (3,'parallel_a_t018_r3',5917.90,DATE'2024-07-01'),
  (4,'parallel_a_t018_r4',68246.68,DATE'2024-11-24'),
  (5,'parallel_a_t018_r5',97348.94,DATE'2024-11-07'),
  (6,'parallel_a_t018_r6',47839.55,DATE'2024-02-22'),
  (7,'parallel_a_t018_r7',43379.79,DATE'2024-06-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_019 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_019 VALUES
  (1,'parallel_a_t019_r1',94439.38,DATE'2024-09-10'),
  (2,'parallel_a_t019_r2',87510.52,DATE'2024-06-13'),
  (3,'parallel_a_t019_r3',91484.37,DATE'2024-09-05'),
  (4,'parallel_a_t019_r4',25244.53,DATE'2024-11-13'),
  (5,'parallel_a_t019_r5',88877.95,DATE'2024-03-20');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_020 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_020 VALUES
  (1,'parallel_a_t020_r1',39546.51,DATE'2024-09-27'),
  (2,'parallel_a_t020_r2',153.38,DATE'2024-05-07'),
  (3,'parallel_a_t020_r3',56446.74,DATE'2024-10-21'),
  (4,'parallel_a_t020_r4',42337.59,DATE'2024-08-15'),
  (5,'parallel_a_t020_r5',88655.27,DATE'2024-09-16'),
  (6,'parallel_a_t020_r6',96563.21,DATE'2024-11-03'),
  (7,'parallel_a_t020_r7',37296.65,DATE'2024-11-21'),
  (8,'parallel_a_t020_r8',81267.42,DATE'2024-02-27'),
  (9,'parallel_a_t020_r9',98553.30,DATE'2024-11-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_021 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_021 VALUES
  (1,'parallel_a_t021_r1',26200.18,DATE'2024-01-02'),
  (2,'parallel_a_t021_r2',32192.60,DATE'2024-10-28'),
  (3,'parallel_a_t021_r3',9645.58,DATE'2024-07-21'),
  (4,'parallel_a_t021_r4',75554.24,DATE'2024-12-23'),
  (5,'parallel_a_t021_r5',50428.63,DATE'2024-07-08'),
  (6,'parallel_a_t021_r6',19442.83,DATE'2024-12-01');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_022 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_022 VALUES
  (1,'parallel_a_t022_r1',55824.28,DATE'2024-03-26'),
  (2,'parallel_a_t022_r2',91314.66,DATE'2024-08-02'),
  (3,'parallel_a_t022_r3',73160.31,DATE'2024-02-15'),
  (4,'parallel_a_t022_r4',17577.59,DATE'2024-11-17'),
  (5,'parallel_a_t022_r5',73359.76,DATE'2024-06-25');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_023 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_023 VALUES
  (1,'parallel_a_t023_r1',80401.92,DATE'2024-09-14'),
  (2,'parallel_a_t023_r2',71910.57,DATE'2024-03-24'),
  (3,'parallel_a_t023_r3',62316.57,DATE'2024-05-25'),
  (4,'parallel_a_t023_r4',32506.81,DATE'2024-05-25'),
  (5,'parallel_a_t023_r5',68427.62,DATE'2024-11-08'),
  (6,'parallel_a_t023_r6',36092.56,DATE'2024-02-23'),
  (7,'parallel_a_t023_r7',37550.30,DATE'2024-05-11'),
  (8,'parallel_a_t023_r8',42004.69,DATE'2024-02-05');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_024 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_024 VALUES
  (1,'parallel_a_t024_r1',30411.49,DATE'2024-12-05'),
  (2,'parallel_a_t024_r2',92691.27,DATE'2024-02-14'),
  (3,'parallel_a_t024_r3',53524.42,DATE'2024-09-15'),
  (4,'parallel_a_t024_r4',54596.07,DATE'2024-04-27'),
  (5,'parallel_a_t024_r5',55169.49,DATE'2024-10-23'),
  (6,'parallel_a_t024_r6',2660.97,DATE'2024-10-13');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_025 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_025 VALUES
  (1,'parallel_a_t025_r1',872.45,DATE'2024-05-25'),
  (2,'parallel_a_t025_r2',51216.53,DATE'2024-09-24'),
  (3,'parallel_a_t025_r3',96389.69,DATE'2024-10-08'),
  (4,'parallel_a_t025_r4',64093.28,DATE'2024-05-14'),
  (5,'parallel_a_t025_r5',63754.03,DATE'2024-07-11'),
  (6,'parallel_a_t025_r6',87770.86,DATE'2024-07-24'),
  (7,'parallel_a_t025_r7',21732.59,DATE'2024-03-20'),
  (8,'parallel_a_t025_r8',70108.03,DATE'2024-07-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_026 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_026 VALUES
  (1,'parallel_a_t026_r1',87000.03,DATE'2024-02-21'),
  (2,'parallel_a_t026_r2',56279.17,DATE'2024-08-06'),
  (3,'parallel_a_t026_r3',6690.33,DATE'2024-07-11'),
  (4,'parallel_a_t026_r4',27842.58,DATE'2024-06-11'),
  (5,'parallel_a_t026_r5',99875.48,DATE'2024-05-25'),
  (6,'parallel_a_t026_r6',55355.32,DATE'2024-02-16'),
  (7,'parallel_a_t026_r7',2640.95,DATE'2024-09-02'),
  (8,'parallel_a_t026_r8',45970.28,DATE'2024-11-03'),
  (9,'parallel_a_t026_r9',85526.05,DATE'2024-01-08');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_027 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_027 VALUES
  (1,'parallel_a_t027_r1',2771.79,DATE'2024-03-08'),
  (2,'parallel_a_t027_r2',16644.60,DATE'2024-11-04'),
  (3,'parallel_a_t027_r3',74020.27,DATE'2024-08-23'),
  (4,'parallel_a_t027_r4',33686.98,DATE'2024-06-06'),
  (5,'parallel_a_t027_r5',79515.77,DATE'2024-12-23'),
  (6,'parallel_a_t027_r6',15112.99,DATE'2024-03-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_028 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_028 VALUES
  (1,'parallel_a_t028_r1',75949.03,DATE'2024-05-19'),
  (2,'parallel_a_t028_r2',88881.48,DATE'2024-07-23'),
  (3,'parallel_a_t028_r3',26095.09,DATE'2024-10-23'),
  (4,'parallel_a_t028_r4',82313.31,DATE'2024-02-23'),
  (5,'parallel_a_t028_r5',39629.87,DATE'2024-10-26');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_029 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_029 VALUES
  (1,'parallel_a_t029_r1',74277.05,DATE'2024-06-18'),
  (2,'parallel_a_t029_r2',56248.84,DATE'2024-06-03'),
  (3,'parallel_a_t029_r3',66417.82,DATE'2024-06-01'),
  (4,'parallel_a_t029_r4',55157.62,DATE'2024-02-14'),
  (5,'parallel_a_t029_r5',47572.81,DATE'2024-08-23');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_030 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_030 VALUES
  (1,'parallel_a_t030_r1',57180.22,DATE'2024-12-17'),
  (2,'parallel_a_t030_r2',85356.34,DATE'2024-10-26'),
  (3,'parallel_a_t030_r3',70639.99,DATE'2024-08-15'),
  (4,'parallel_a_t030_r4',57191.93,DATE'2024-10-09'),
  (5,'parallel_a_t030_r5',42345.31,DATE'2024-02-09'),
  (6,'parallel_a_t030_r6',59187.31,DATE'2024-08-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_031 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_031 VALUES
  (1,'parallel_a_t031_r1',87680.48,DATE'2024-06-01'),
  (2,'parallel_a_t031_r2',64889.41,DATE'2024-03-16'),
  (3,'parallel_a_t031_r3',27902.45,DATE'2024-05-11'),
  (4,'parallel_a_t031_r4',36755.76,DATE'2024-12-09'),
  (5,'parallel_a_t031_r5',72948.01,DATE'2024-09-07'),
  (6,'parallel_a_t031_r6',11321.30,DATE'2024-12-14'),
  (7,'parallel_a_t031_r7',64138.71,DATE'2024-04-23'),
  (8,'parallel_a_t031_r8',62502.82,DATE'2024-12-16'),
  (9,'parallel_a_t031_r9',58843.02,DATE'2024-02-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_032 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_032 VALUES
  (1,'parallel_a_t032_r1',53105.88,DATE'2024-04-10'),
  (2,'parallel_a_t032_r2',87126.74,DATE'2024-06-16'),
  (3,'parallel_a_t032_r3',72644.67,DATE'2024-06-14'),
  (4,'parallel_a_t032_r4',97871.70,DATE'2024-06-12'),
  (5,'parallel_a_t032_r5',92224.58,DATE'2024-05-10'),
  (6,'parallel_a_t032_r6',33051.29,DATE'2024-02-24');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_033 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_033 VALUES
  (1,'parallel_a_t033_r1',41459.15,DATE'2024-12-18'),
  (2,'parallel_a_t033_r2',90561.23,DATE'2024-04-07'),
  (3,'parallel_a_t033_r3',96910.61,DATE'2024-05-24'),
  (4,'parallel_a_t033_r4',77378.97,DATE'2024-09-20'),
  (5,'parallel_a_t033_r5',37193.12,DATE'2024-04-10'),
  (6,'parallel_a_t033_r6',29916.46,DATE'2024-03-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_034 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_034 VALUES
  (1,'parallel_a_t034_r1',92901.68,DATE'2024-03-09'),
  (2,'parallel_a_t034_r2',6065.06,DATE'2024-09-10'),
  (3,'parallel_a_t034_r3',91511.16,DATE'2024-11-28'),
  (4,'parallel_a_t034_r4',98769.62,DATE'2024-02-28'),
  (5,'parallel_a_t034_r5',1707.73,DATE'2024-05-16');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_035 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_035 VALUES
  (1,'parallel_a_t035_r1',57833.43,DATE'2024-03-02'),
  (2,'parallel_a_t035_r2',33192.61,DATE'2024-02-27'),
  (3,'parallel_a_t035_r3',8664.51,DATE'2024-08-03'),
  (4,'parallel_a_t035_r4',75731.80,DATE'2024-11-02'),
  (5,'parallel_a_t035_r5',19987.19,DATE'2024-10-10'),
  (6,'parallel_a_t035_r6',11264.31,DATE'2024-02-18'),
  (7,'parallel_a_t035_r7',54648.77,DATE'2024-10-26'),
  (8,'parallel_a_t035_r8',81157.28,DATE'2024-09-13');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_036 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_036 VALUES
  (1,'parallel_a_t036_r1',58128.38,DATE'2024-10-14'),
  (2,'parallel_a_t036_r2',40126.72,DATE'2024-10-02'),
  (3,'parallel_a_t036_r3',80005.94,DATE'2024-02-25'),
  (4,'parallel_a_t036_r4',27335.80,DATE'2024-04-09'),
  (5,'parallel_a_t036_r5',86663.10,DATE'2024-03-08'),
  (6,'parallel_a_t036_r6',22882.70,DATE'2024-02-06'),
  (7,'parallel_a_t036_r7',450.52,DATE'2024-08-23'),
  (8,'parallel_a_t036_r8',77932.60,DATE'2024-05-02');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_037 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_037 VALUES
  (1,'parallel_a_t037_r1',37862.90,DATE'2024-05-23'),
  (2,'parallel_a_t037_r2',59610.09,DATE'2024-11-08'),
  (3,'parallel_a_t037_r3',34775.80,DATE'2024-10-22'),
  (4,'parallel_a_t037_r4',26028.54,DATE'2024-02-18'),
  (5,'parallel_a_t037_r5',29566.82,DATE'2024-03-09'),
  (6,'parallel_a_t037_r6',18743.09,DATE'2024-01-06');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_038 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_038 VALUES
  (1,'parallel_a_t038_r1',78093.95,DATE'2024-10-10'),
  (2,'parallel_a_t038_r2',57660.15,DATE'2024-08-23'),
  (3,'parallel_a_t038_r3',39957.89,DATE'2024-07-09'),
  (4,'parallel_a_t038_r4',65697.69,DATE'2024-08-15'),
  (5,'parallel_a_t038_r5',10643.76,DATE'2024-01-14'),
  (6,'parallel_a_t038_r6',96376.41,DATE'2024-10-09'),
  (7,'parallel_a_t038_r7',3490.11,DATE'2024-04-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_a.t_039 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_a.t_039 VALUES
  (1,'parallel_a_t039_r1',77055.02,DATE'2024-11-27'),
  (2,'parallel_a_t039_r2',35425.73,DATE'2024-01-25'),
  (3,'parallel_a_t039_r3',99225.22,DATE'2024-08-17'),
  (4,'parallel_a_t039_r4',85494.56,DATE'2024-05-06'),
  (5,'parallel_a_t039_r5',76820.55,DATE'2024-11-27'),
  (6,'parallel_a_t039_r6',64551.11,DATE'2024-08-12'),
  (7,'parallel_a_t039_r7',53623.42,DATE'2024-06-22'),
  (8,'parallel_a_t039_r8',13810.20,DATE'2024-06-14'),
  (9,'parallel_a_t039_r9',91021.63,DATE'2024-05-22');

CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b COMMENT 'parallelism stress-test schema';
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_000 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_000 VALUES
  (1,'parallel_b_t000_r1',99780.70,DATE'2024-01-15'),
  (2,'parallel_b_t000_r2',11642.40,DATE'2024-05-11'),
  (3,'parallel_b_t000_r3',15294.98,DATE'2024-07-28'),
  (4,'parallel_b_t000_r4',67549.00,DATE'2024-11-28'),
  (5,'parallel_b_t000_r5',71218.59,DATE'2024-07-02'),
  (6,'parallel_b_t000_r6',24686.66,DATE'2024-06-20'),
  (7,'parallel_b_t000_r7',99248.63,DATE'2024-11-15'),
  (8,'parallel_b_t000_r8',99713.06,DATE'2024-04-09');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_001 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_001 VALUES
  (1,'parallel_b_t001_r1',17272.36,DATE'2024-08-23'),
  (2,'parallel_b_t001_r2',63632.15,DATE'2024-01-21'),
  (3,'parallel_b_t001_r3',79906.30,DATE'2024-12-06'),
  (4,'parallel_b_t001_r4',40830.70,DATE'2024-01-18'),
  (5,'parallel_b_t001_r5',53576.11,DATE'2024-04-27'),
  (6,'parallel_b_t001_r6',14971.59,DATE'2024-02-21'),
  (7,'parallel_b_t001_r7',20281.63,DATE'2024-12-10'),
  (8,'parallel_b_t001_r8',66798.90,DATE'2024-05-14'),
  (9,'parallel_b_t001_r9',63342.60,DATE'2024-04-15');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_002 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_002 VALUES
  (1,'parallel_b_t002_r1',19058.49,DATE'2024-04-20'),
  (2,'parallel_b_t002_r2',66716.95,DATE'2024-03-28'),
  (3,'parallel_b_t002_r3',9250.35,DATE'2024-07-11'),
  (4,'parallel_b_t002_r4',66650.34,DATE'2024-01-10'),
  (5,'parallel_b_t002_r5',95264.38,DATE'2024-10-19'),
  (6,'parallel_b_t002_r6',86599.62,DATE'2024-03-15'),
  (7,'parallel_b_t002_r7',70692.61,DATE'2024-06-11'),
  (8,'parallel_b_t002_r8',72440.97,DATE'2024-09-13'),
  (9,'parallel_b_t002_r9',59782.41,DATE'2024-04-23');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_003 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_003 VALUES
  (1,'parallel_b_t003_r1',75046.49,DATE'2024-04-28'),
  (2,'parallel_b_t003_r2',53941.05,DATE'2024-06-24'),
  (3,'parallel_b_t003_r3',62093.90,DATE'2024-07-13'),
  (4,'parallel_b_t003_r4',87091.83,DATE'2024-03-16'),
  (5,'parallel_b_t003_r5',4952.16,DATE'2024-09-19'),
  (6,'parallel_b_t003_r6',43615.12,DATE'2024-08-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_004 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_004 VALUES
  (1,'parallel_b_t004_r1',59990.01,DATE'2024-12-05'),
  (2,'parallel_b_t004_r2',53836.83,DATE'2024-03-03'),
  (3,'parallel_b_t004_r3',61637.33,DATE'2024-06-20'),
  (4,'parallel_b_t004_r4',90894.50,DATE'2024-11-03'),
  (5,'parallel_b_t004_r5',43165.86,DATE'2024-09-13'),
  (6,'parallel_b_t004_r6',41605.80,DATE'2024-12-25'),
  (7,'parallel_b_t004_r7',64056.69,DATE'2024-01-20'),
  (8,'parallel_b_t004_r8',9068.30,DATE'2024-11-22'),
  (9,'parallel_b_t004_r9',37767.29,DATE'2024-12-03');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_005 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_005 VALUES
  (1,'parallel_b_t005_r1',13003.97,DATE'2024-11-23'),
  (2,'parallel_b_t005_r2',13273.56,DATE'2024-03-23'),
  (3,'parallel_b_t005_r3',39351.03,DATE'2024-01-11'),
  (4,'parallel_b_t005_r4',7455.37,DATE'2024-06-12'),
  (5,'parallel_b_t005_r5',56550.18,DATE'2024-04-17'),
  (6,'parallel_b_t005_r6',54108.72,DATE'2024-11-26'),
  (7,'parallel_b_t005_r7',23700.21,DATE'2024-03-03'),
  (8,'parallel_b_t005_r8',79987.48,DATE'2024-10-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_006 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_006 VALUES
  (1,'parallel_b_t006_r1',65330.74,DATE'2024-03-08'),
  (2,'parallel_b_t006_r2',60543.81,DATE'2024-05-15'),
  (3,'parallel_b_t006_r3',33566.85,DATE'2024-01-26'),
  (4,'parallel_b_t006_r4',61081.36,DATE'2024-11-18'),
  (5,'parallel_b_t006_r5',20806.09,DATE'2024-08-12'),
  (6,'parallel_b_t006_r6',77114.38,DATE'2024-11-14');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_007 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_007 VALUES
  (1,'parallel_b_t007_r1',32880.58,DATE'2024-05-07'),
  (2,'parallel_b_t007_r2',50522.61,DATE'2024-02-08'),
  (3,'parallel_b_t007_r3',50086.73,DATE'2024-06-19'),
  (4,'parallel_b_t007_r4',38880.89,DATE'2024-05-01'),
  (5,'parallel_b_t007_r5',86375.50,DATE'2024-05-01'),
  (6,'parallel_b_t007_r6',74277.87,DATE'2024-12-02'),
  (7,'parallel_b_t007_r7',79582.95,DATE'2024-08-27'),
  (8,'parallel_b_t007_r8',37612.99,DATE'2024-04-20'),
  (9,'parallel_b_t007_r9',46279.28,DATE'2024-11-07'),
  (10,'parallel_b_t007_r10',81481.32,DATE'2024-11-25');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_008 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_008 VALUES
  (1,'parallel_b_t008_r1',86500.87,DATE'2024-03-21'),
  (2,'parallel_b_t008_r2',12835.80,DATE'2024-11-02'),
  (3,'parallel_b_t008_r3',40592.56,DATE'2024-01-19'),
  (4,'parallel_b_t008_r4',47923.93,DATE'2024-03-03'),
  (5,'parallel_b_t008_r5',38780.41,DATE'2024-12-14'),
  (6,'parallel_b_t008_r6',23121.25,DATE'2024-03-26'),
  (7,'parallel_b_t008_r7',70801.46,DATE'2024-09-17'),
  (8,'parallel_b_t008_r8',35820.21,DATE'2024-05-27'),
  (9,'parallel_b_t008_r9',63256.37,DATE'2024-12-28'),
  (10,'parallel_b_t008_r10',44496.14,DATE'2024-08-03');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_009 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_009 VALUES
  (1,'parallel_b_t009_r1',98948.28,DATE'2024-11-24'),
  (2,'parallel_b_t009_r2',88495.50,DATE'2024-09-12'),
  (3,'parallel_b_t009_r3',11936.50,DATE'2024-01-09'),
  (4,'parallel_b_t009_r4',70429.15,DATE'2024-08-12'),
  (5,'parallel_b_t009_r5',88288.95,DATE'2024-11-09'),
  (6,'parallel_b_t009_r6',76719.48,DATE'2024-11-12');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_010 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_010 VALUES
  (1,'parallel_b_t010_r1',88552.29,DATE'2024-08-01'),
  (2,'parallel_b_t010_r2',81305.71,DATE'2024-06-20'),
  (3,'parallel_b_t010_r3',29115.82,DATE'2024-02-21'),
  (4,'parallel_b_t010_r4',60952.89,DATE'2024-05-21'),
  (5,'parallel_b_t010_r5',53616.14,DATE'2024-03-02');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_011 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_011 VALUES
  (1,'parallel_b_t011_r1',39993.63,DATE'2024-02-04'),
  (2,'parallel_b_t011_r2',30875.68,DATE'2024-03-13'),
  (3,'parallel_b_t011_r3',59559.47,DATE'2024-11-24'),
  (4,'parallel_b_t011_r4',91389.69,DATE'2024-07-19'),
  (5,'parallel_b_t011_r5',97390.93,DATE'2024-03-14');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_012 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_012 VALUES
  (1,'parallel_b_t012_r1',13078.62,DATE'2024-10-14'),
  (2,'parallel_b_t012_r2',36766.04,DATE'2024-12-12'),
  (3,'parallel_b_t012_r3',28577.56,DATE'2024-08-08'),
  (4,'parallel_b_t012_r4',47637.12,DATE'2024-11-12'),
  (5,'parallel_b_t012_r5',71464.82,DATE'2024-06-02'),
  (6,'parallel_b_t012_r6',52284.35,DATE'2024-04-04'),
  (7,'parallel_b_t012_r7',59700.11,DATE'2024-11-07'),
  (8,'parallel_b_t012_r8',84209.81,DATE'2024-10-01'),
  (9,'parallel_b_t012_r9',6729.42,DATE'2024-04-05'),
  (10,'parallel_b_t012_r10',74100.26,DATE'2024-02-27');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_013 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_013 VALUES
  (1,'parallel_b_t013_r1',27252.75,DATE'2024-04-27'),
  (2,'parallel_b_t013_r2',30639.42,DATE'2024-03-26'),
  (3,'parallel_b_t013_r3',78216.00,DATE'2024-05-28'),
  (4,'parallel_b_t013_r4',19064.16,DATE'2024-09-09'),
  (5,'parallel_b_t013_r5',22971.14,DATE'2024-11-28'),
  (6,'parallel_b_t013_r6',3479.16,DATE'2024-01-12'),
  (7,'parallel_b_t013_r7',31286.75,DATE'2024-06-01'),
  (8,'parallel_b_t013_r8',22938.33,DATE'2024-01-05'),
  (9,'parallel_b_t013_r9',97318.53,DATE'2024-09-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_014 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_014 VALUES
  (1,'parallel_b_t014_r1',8430.60,DATE'2024-08-25'),
  (2,'parallel_b_t014_r2',47553.65,DATE'2024-10-04'),
  (3,'parallel_b_t014_r3',59345.64,DATE'2024-04-20'),
  (4,'parallel_b_t014_r4',5783.93,DATE'2024-11-17'),
  (5,'parallel_b_t014_r5',39635.58,DATE'2024-11-01'),
  (6,'parallel_b_t014_r6',8072.61,DATE'2024-07-14'),
  (7,'parallel_b_t014_r7',90028.13,DATE'2024-08-23'),
  (8,'parallel_b_t014_r8',58238.09,DATE'2024-02-11'),
  (9,'parallel_b_t014_r9',79830.18,DATE'2024-02-05'),
  (10,'parallel_b_t014_r10',36146.79,DATE'2024-11-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_015 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_015 VALUES
  (1,'parallel_b_t015_r1',93443.41,DATE'2024-07-20'),
  (2,'parallel_b_t015_r2',69641.37,DATE'2024-08-17'),
  (3,'parallel_b_t015_r3',79454.55,DATE'2024-02-26'),
  (4,'parallel_b_t015_r4',92083.14,DATE'2024-11-21'),
  (5,'parallel_b_t015_r5',72364.92,DATE'2024-04-14'),
  (6,'parallel_b_t015_r6',59286.29,DATE'2024-07-11'),
  (7,'parallel_b_t015_r7',59541.51,DATE'2024-07-24'),
  (8,'parallel_b_t015_r8',12563.40,DATE'2024-07-11'),
  (9,'parallel_b_t015_r9',87277.32,DATE'2024-06-05');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_016 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_016 VALUES
  (1,'parallel_b_t016_r1',62263.08,DATE'2024-02-27'),
  (2,'parallel_b_t016_r2',11288.11,DATE'2024-07-04'),
  (3,'parallel_b_t016_r3',97694.94,DATE'2024-06-26'),
  (4,'parallel_b_t016_r4',17154.71,DATE'2024-01-19'),
  (5,'parallel_b_t016_r5',73708.71,DATE'2024-06-22'),
  (6,'parallel_b_t016_r6',16121.52,DATE'2024-06-28'),
  (7,'parallel_b_t016_r7',87307.96,DATE'2024-07-28'),
  (8,'parallel_b_t016_r8',94610.06,DATE'2024-05-20'),
  (9,'parallel_b_t016_r9',41053.45,DATE'2024-02-19'),
  (10,'parallel_b_t016_r10',66607.27,DATE'2024-03-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_017 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_017 VALUES
  (1,'parallel_b_t017_r1',29492.13,DATE'2024-06-28'),
  (2,'parallel_b_t017_r2',73010.47,DATE'2024-02-25'),
  (3,'parallel_b_t017_r3',36612.73,DATE'2024-04-26'),
  (4,'parallel_b_t017_r4',56344.71,DATE'2024-10-20'),
  (5,'parallel_b_t017_r5',88570.82,DATE'2024-09-01'),
  (6,'parallel_b_t017_r6',79922.84,DATE'2024-12-09'),
  (7,'parallel_b_t017_r7',3894.23,DATE'2024-05-23'),
  (8,'parallel_b_t017_r8',40599.43,DATE'2024-06-01');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_018 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_018 VALUES
  (1,'parallel_b_t018_r1',18876.72,DATE'2024-11-13'),
  (2,'parallel_b_t018_r2',9220.18,DATE'2024-12-21'),
  (3,'parallel_b_t018_r3',4118.11,DATE'2024-12-17'),
  (4,'parallel_b_t018_r4',28297.48,DATE'2024-07-15'),
  (5,'parallel_b_t018_r5',44781.20,DATE'2024-06-10'),
  (6,'parallel_b_t018_r6',94681.41,DATE'2024-10-20');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_019 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_019 VALUES
  (1,'parallel_b_t019_r1',6995.19,DATE'2024-03-25'),
  (2,'parallel_b_t019_r2',81081.06,DATE'2024-11-03'),
  (3,'parallel_b_t019_r3',35765.56,DATE'2024-11-14'),
  (4,'parallel_b_t019_r4',63756.77,DATE'2024-08-14'),
  (5,'parallel_b_t019_r5',35904.27,DATE'2024-09-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_020 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_020 VALUES
  (1,'parallel_b_t020_r1',56449.14,DATE'2024-05-22'),
  (2,'parallel_b_t020_r2',89030.75,DATE'2024-08-17'),
  (3,'parallel_b_t020_r3',87542.39,DATE'2024-01-08'),
  (4,'parallel_b_t020_r4',51907.76,DATE'2024-01-01'),
  (5,'parallel_b_t020_r5',26894.38,DATE'2024-04-25'),
  (6,'parallel_b_t020_r6',18087.97,DATE'2024-05-10'),
  (7,'parallel_b_t020_r7',43107.15,DATE'2024-01-16');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_021 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_021 VALUES
  (1,'parallel_b_t021_r1',56546.22,DATE'2024-03-13'),
  (2,'parallel_b_t021_r2',69906.90,DATE'2024-04-17'),
  (3,'parallel_b_t021_r3',73333.85,DATE'2024-06-03'),
  (4,'parallel_b_t021_r4',52144.94,DATE'2024-01-14'),
  (5,'parallel_b_t021_r5',2556.58,DATE'2024-02-28'),
  (6,'parallel_b_t021_r6',41133.73,DATE'2024-07-19'),
  (7,'parallel_b_t021_r7',53108.90,DATE'2024-11-14'),
  (8,'parallel_b_t021_r8',38047.14,DATE'2024-07-01'),
  (9,'parallel_b_t021_r9',42668.21,DATE'2024-10-15'),
  (10,'parallel_b_t021_r10',90488.46,DATE'2024-02-14');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_022 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_022 VALUES
  (1,'parallel_b_t022_r1',31991.55,DATE'2024-10-13'),
  (2,'parallel_b_t022_r2',68776.10,DATE'2024-07-28'),
  (3,'parallel_b_t022_r3',40768.95,DATE'2024-06-08'),
  (4,'parallel_b_t022_r4',43755.99,DATE'2024-03-03'),
  (5,'parallel_b_t022_r5',67010.81,DATE'2024-02-17');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_023 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_023 VALUES
  (1,'parallel_b_t023_r1',25515.99,DATE'2024-06-12'),
  (2,'parallel_b_t023_r2',95439.82,DATE'2024-03-08'),
  (3,'parallel_b_t023_r3',13574.18,DATE'2024-05-07'),
  (4,'parallel_b_t023_r4',22840.77,DATE'2024-03-25'),
  (5,'parallel_b_t023_r5',99603.83,DATE'2024-02-06'),
  (6,'parallel_b_t023_r6',82406.63,DATE'2024-08-25'),
  (7,'parallel_b_t023_r7',73989.97,DATE'2024-10-15'),
  (8,'parallel_b_t023_r8',89377.72,DATE'2024-11-21'),
  (9,'parallel_b_t023_r9',81957.41,DATE'2024-11-11');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_024 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_024 VALUES
  (1,'parallel_b_t024_r1',57740.08,DATE'2024-08-15'),
  (2,'parallel_b_t024_r2',82856.38,DATE'2024-05-19'),
  (3,'parallel_b_t024_r3',7462.45,DATE'2024-09-03'),
  (4,'parallel_b_t024_r4',40785.59,DATE'2024-08-02'),
  (5,'parallel_b_t024_r5',7556.47,DATE'2024-05-03'),
  (6,'parallel_b_t024_r6',84597.11,DATE'2024-10-20');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_025 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_025 VALUES
  (1,'parallel_b_t025_r1',50494.59,DATE'2024-10-18'),
  (2,'parallel_b_t025_r2',96951.05,DATE'2024-08-26'),
  (3,'parallel_b_t025_r3',75028.83,DATE'2024-04-11'),
  (4,'parallel_b_t025_r4',79399.60,DATE'2024-09-05'),
  (5,'parallel_b_t025_r5',8209.57,DATE'2024-02-26'),
  (6,'parallel_b_t025_r6',45115.91,DATE'2024-02-17'),
  (7,'parallel_b_t025_r7',84789.22,DATE'2024-01-08'),
  (8,'parallel_b_t025_r8',92849.56,DATE'2024-08-17'),
  (9,'parallel_b_t025_r9',68617.78,DATE'2024-03-12');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_026 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_026 VALUES
  (1,'parallel_b_t026_r1',37179.49,DATE'2024-07-25'),
  (2,'parallel_b_t026_r2',44450.86,DATE'2024-10-02'),
  (3,'parallel_b_t026_r3',82791.82,DATE'2024-06-03'),
  (4,'parallel_b_t026_r4',43313.12,DATE'2024-09-22'),
  (5,'parallel_b_t026_r5',50767.36,DATE'2024-05-24'),
  (6,'parallel_b_t026_r6',86157.77,DATE'2024-03-11'),
  (7,'parallel_b_t026_r7',10782.74,DATE'2024-11-05');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_027 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_027 VALUES
  (1,'parallel_b_t027_r1',40762.83,DATE'2024-12-22'),
  (2,'parallel_b_t027_r2',51474.16,DATE'2024-10-23'),
  (3,'parallel_b_t027_r3',11204.39,DATE'2024-09-13'),
  (4,'parallel_b_t027_r4',84443.42,DATE'2024-03-22'),
  (5,'parallel_b_t027_r5',92221.94,DATE'2024-11-17'),
  (6,'parallel_b_t027_r6',12349.82,DATE'2024-11-14'),
  (7,'parallel_b_t027_r7',66748.46,DATE'2024-01-12');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_028 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_028 VALUES
  (1,'parallel_b_t028_r1',23727.27,DATE'2024-06-25'),
  (2,'parallel_b_t028_r2',63836.24,DATE'2024-04-05'),
  (3,'parallel_b_t028_r3',20408.09,DATE'2024-05-28'),
  (4,'parallel_b_t028_r4',13356.64,DATE'2024-09-27'),
  (5,'parallel_b_t028_r5',96915.67,DATE'2024-01-22'),
  (6,'parallel_b_t028_r6',44240.98,DATE'2024-10-05'),
  (7,'parallel_b_t028_r7',78382.48,DATE'2024-03-06');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_029 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_029 VALUES
  (1,'parallel_b_t029_r1',90912.98,DATE'2024-10-26'),
  (2,'parallel_b_t029_r2',21797.92,DATE'2024-08-02'),
  (3,'parallel_b_t029_r3',53952.46,DATE'2024-11-24'),
  (4,'parallel_b_t029_r4',31233.56,DATE'2024-10-10'),
  (5,'parallel_b_t029_r5',98717.95,DATE'2024-08-08'),
  (6,'parallel_b_t029_r6',70093.30,DATE'2024-05-26');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_030 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_030 VALUES
  (1,'parallel_b_t030_r1',25530.47,DATE'2024-11-19'),
  (2,'parallel_b_t030_r2',57849.59,DATE'2024-05-25'),
  (3,'parallel_b_t030_r3',50152.64,DATE'2024-09-14'),
  (4,'parallel_b_t030_r4',21341.25,DATE'2024-10-05'),
  (5,'parallel_b_t030_r5',32868.06,DATE'2024-11-16'),
  (6,'parallel_b_t030_r6',48755.70,DATE'2024-02-23'),
  (7,'parallel_b_t030_r7',67721.15,DATE'2024-05-03'),
  (8,'parallel_b_t030_r8',21112.34,DATE'2024-08-17');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_031 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_031 VALUES
  (1,'parallel_b_t031_r1',57440.11,DATE'2024-04-27'),
  (2,'parallel_b_t031_r2',59230.44,DATE'2024-01-14'),
  (3,'parallel_b_t031_r3',7080.50,DATE'2024-09-12'),
  (4,'parallel_b_t031_r4',31003.49,DATE'2024-02-12'),
  (5,'parallel_b_t031_r5',29530.03,DATE'2024-06-04'),
  (6,'parallel_b_t031_r6',93777.83,DATE'2024-06-26');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_032 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_032 VALUES
  (1,'parallel_b_t032_r1',18135.04,DATE'2024-05-27'),
  (2,'parallel_b_t032_r2',62025.89,DATE'2024-03-25'),
  (3,'parallel_b_t032_r3',92576.60,DATE'2024-08-20'),
  (4,'parallel_b_t032_r4',788.10,DATE'2024-01-09'),
  (5,'parallel_b_t032_r5',28366.19,DATE'2024-09-24'),
  (6,'parallel_b_t032_r6',79907.67,DATE'2024-07-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_033 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_033 VALUES
  (1,'parallel_b_t033_r1',31247.38,DATE'2024-02-02'),
  (2,'parallel_b_t033_r2',31352.53,DATE'2024-11-26'),
  (3,'parallel_b_t033_r3',81733.58,DATE'2024-02-04'),
  (4,'parallel_b_t033_r4',65613.76,DATE'2024-09-01'),
  (5,'parallel_b_t033_r5',82931.65,DATE'2024-10-08'),
  (6,'parallel_b_t033_r6',94281.18,DATE'2024-05-14'),
  (7,'parallel_b_t033_r7',305.78,DATE'2024-06-08');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_034 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_034 VALUES
  (1,'parallel_b_t034_r1',54700.23,DATE'2024-11-22'),
  (2,'parallel_b_t034_r2',11321.67,DATE'2024-06-03'),
  (3,'parallel_b_t034_r3',69051.69,DATE'2024-09-26'),
  (4,'parallel_b_t034_r4',66635.70,DATE'2024-01-13'),
  (5,'parallel_b_t034_r5',61721.05,DATE'2024-11-13'),
  (6,'parallel_b_t034_r6',49029.32,DATE'2024-12-01'),
  (7,'parallel_b_t034_r7',46905.08,DATE'2024-06-08'),
  (8,'parallel_b_t034_r8',96168.84,DATE'2024-11-04'),
  (9,'parallel_b_t034_r9',76371.94,DATE'2024-06-05');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_035 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_035 VALUES
  (1,'parallel_b_t035_r1',46276.69,DATE'2024-06-27'),
  (2,'parallel_b_t035_r2',84352.22,DATE'2024-11-15'),
  (3,'parallel_b_t035_r3',91246.61,DATE'2024-11-06'),
  (4,'parallel_b_t035_r4',17775.08,DATE'2024-12-25'),
  (5,'parallel_b_t035_r5',60088.04,DATE'2024-05-07');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_036 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_036 VALUES
  (1,'parallel_b_t036_r1',26244.05,DATE'2024-06-10'),
  (2,'parallel_b_t036_r2',67652.50,DATE'2024-09-16'),
  (3,'parallel_b_t036_r3',33302.04,DATE'2024-11-07'),
  (4,'parallel_b_t036_r4',37601.45,DATE'2024-01-28'),
  (5,'parallel_b_t036_r5',86048.42,DATE'2024-05-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_037 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_037 VALUES
  (1,'parallel_b_t037_r1',57372.51,DATE'2024-12-15'),
  (2,'parallel_b_t037_r2',50778.43,DATE'2024-03-16'),
  (3,'parallel_b_t037_r3',90810.63,DATE'2024-06-26'),
  (4,'parallel_b_t037_r4',68162.34,DATE'2024-02-24'),
  (5,'parallel_b_t037_r5',55740.10,DATE'2024-07-20'),
  (6,'parallel_b_t037_r6',23752.69,DATE'2024-05-11'),
  (7,'parallel_b_t037_r7',13547.10,DATE'2024-06-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_038 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_038 VALUES
  (1,'parallel_b_t038_r1',40273.57,DATE'2024-10-23'),
  (2,'parallel_b_t038_r2',55951.21,DATE'2024-12-15'),
  (3,'parallel_b_t038_r3',46177.57,DATE'2024-01-24'),
  (4,'parallel_b_t038_r4',46310.78,DATE'2024-07-09'),
  (5,'parallel_b_t038_r5',83916.07,DATE'2024-02-22'),
  (6,'parallel_b_t038_r6',83660.51,DATE'2024-06-17'),
  (7,'parallel_b_t038_r7',98390.86,DATE'2024-03-01');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_b.t_039 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_b.t_039 VALUES
  (1,'parallel_b_t039_r1',79735.86,DATE'2024-08-02'),
  (2,'parallel_b_t039_r2',16645.08,DATE'2024-04-25'),
  (3,'parallel_b_t039_r3',84662.46,DATE'2024-06-13'),
  (4,'parallel_b_t039_r4',74449.04,DATE'2024-10-05'),
  (5,'parallel_b_t039_r5',89106.57,DATE'2024-06-12'),
  (6,'parallel_b_t039_r6',58288.97,DATE'2024-02-19');

CREATE SCHEMA IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c COMMENT 'parallelism stress-test schema';
CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_000 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_000 VALUES
  (1,'parallel_c_t000_r1',69497.46,DATE'2024-07-11'),
  (2,'parallel_c_t000_r2',85239.35,DATE'2024-04-04'),
  (3,'parallel_c_t000_r3',3496.94,DATE'2024-03-16'),
  (4,'parallel_c_t000_r4',67961.49,DATE'2024-09-04'),
  (5,'parallel_c_t000_r5',34411.99,DATE'2024-05-23'),
  (6,'parallel_c_t000_r6',58593.27,DATE'2024-10-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_001 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_001 VALUES
  (1,'parallel_c_t001_r1',64482.25,DATE'2024-02-05'),
  (2,'parallel_c_t001_r2',9806.57,DATE'2024-03-23'),
  (3,'parallel_c_t001_r3',58441.11,DATE'2024-11-28'),
  (4,'parallel_c_t001_r4',41989.85,DATE'2024-06-23'),
  (5,'parallel_c_t001_r5',8611.70,DATE'2024-09-10'),
  (6,'parallel_c_t001_r6',39415.20,DATE'2024-12-23'),
  (7,'parallel_c_t001_r7',91879.81,DATE'2024-03-26'),
  (8,'parallel_c_t001_r8',47481.65,DATE'2024-04-04'),
  (9,'parallel_c_t001_r9',26442.17,DATE'2024-04-26'),
  (10,'parallel_c_t001_r10',64852.03,DATE'2024-06-18');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_002 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_002 VALUES
  (1,'parallel_c_t002_r1',48453.59,DATE'2024-09-05'),
  (2,'parallel_c_t002_r2',80309.11,DATE'2024-02-10'),
  (3,'parallel_c_t002_r3',52287.91,DATE'2024-12-16'),
  (4,'parallel_c_t002_r4',69003.52,DATE'2024-07-27'),
  (5,'parallel_c_t002_r5',75453.09,DATE'2024-03-11'),
  (6,'parallel_c_t002_r6',84292.09,DATE'2024-08-15'),
  (7,'parallel_c_t002_r7',89243.66,DATE'2024-06-05'),
  (8,'parallel_c_t002_r8',72360.81,DATE'2024-10-06'),
  (9,'parallel_c_t002_r9',17012.55,DATE'2024-09-28');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_003 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_003 VALUES
  (1,'parallel_c_t003_r1',16374.66,DATE'2024-03-10'),
  (2,'parallel_c_t003_r2',21681.20,DATE'2024-06-23'),
  (3,'parallel_c_t003_r3',29652.44,DATE'2024-09-10'),
  (4,'parallel_c_t003_r4',10436.32,DATE'2024-04-21'),
  (5,'parallel_c_t003_r5',72299.35,DATE'2024-03-21');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_004 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_004 VALUES
  (1,'parallel_c_t004_r1',80634.68,DATE'2024-02-17'),
  (2,'parallel_c_t004_r2',84115.21,DATE'2024-10-19'),
  (3,'parallel_c_t004_r3',20310.21,DATE'2024-11-20'),
  (4,'parallel_c_t004_r4',94554.77,DATE'2024-06-27'),
  (5,'parallel_c_t004_r5',73974.05,DATE'2024-01-03'),
  (6,'parallel_c_t004_r6',6058.82,DATE'2024-10-09'),
  (7,'parallel_c_t004_r7',85444.26,DATE'2024-10-14');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_005 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_005 VALUES
  (1,'parallel_c_t005_r1',83853.03,DATE'2024-08-21'),
  (2,'parallel_c_t005_r2',71609.37,DATE'2024-11-10'),
  (3,'parallel_c_t005_r3',63400.31,DATE'2024-11-13'),
  (4,'parallel_c_t005_r4',39085.58,DATE'2024-02-23'),
  (5,'parallel_c_t005_r5',7952.20,DATE'2024-08-14'),
  (6,'parallel_c_t005_r6',63571.59,DATE'2024-04-11'),
  (7,'parallel_c_t005_r7',79616.18,DATE'2024-06-28'),
  (8,'parallel_c_t005_r8',94257.40,DATE'2024-12-28'),
  (9,'parallel_c_t005_r9',45368.51,DATE'2024-03-25');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_006 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_006 VALUES
  (1,'parallel_c_t006_r1',67599.71,DATE'2024-02-11'),
  (2,'parallel_c_t006_r2',31785.59,DATE'2024-02-09'),
  (3,'parallel_c_t006_r3',59029.31,DATE'2024-03-04'),
  (4,'parallel_c_t006_r4',6732.37,DATE'2024-07-28'),
  (5,'parallel_c_t006_r5',80758.53,DATE'2024-04-28'),
  (6,'parallel_c_t006_r6',21032.41,DATE'2024-10-24'),
  (7,'parallel_c_t006_r7',41088.24,DATE'2024-03-16');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_007 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_007 VALUES
  (1,'parallel_c_t007_r1',61286.63,DATE'2024-05-16'),
  (2,'parallel_c_t007_r2',3137.11,DATE'2024-07-17'),
  (3,'parallel_c_t007_r3',60030.30,DATE'2024-04-19'),
  (4,'parallel_c_t007_r4',46354.06,DATE'2024-01-10'),
  (5,'parallel_c_t007_r5',64966.76,DATE'2024-11-22'),
  (6,'parallel_c_t007_r6',61780.36,DATE'2024-09-01'),
  (7,'parallel_c_t007_r7',14189.55,DATE'2024-03-09'),
  (8,'parallel_c_t007_r8',95455.46,DATE'2024-07-12'),
  (9,'parallel_c_t007_r9',6030.51,DATE'2024-01-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_008 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_008 VALUES
  (1,'parallel_c_t008_r1',25613.46,DATE'2024-09-10'),
  (2,'parallel_c_t008_r2',9737.49,DATE'2024-09-15'),
  (3,'parallel_c_t008_r3',72170.35,DATE'2024-10-22'),
  (4,'parallel_c_t008_r4',80190.15,DATE'2024-03-04'),
  (5,'parallel_c_t008_r5',51729.47,DATE'2024-06-18'),
  (6,'parallel_c_t008_r6',48020.96,DATE'2024-03-07'),
  (7,'parallel_c_t008_r7',79066.65,DATE'2024-07-17'),
  (8,'parallel_c_t008_r8',5376.05,DATE'2024-01-05'),
  (9,'parallel_c_t008_r9',93631.42,DATE'2024-08-17');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_009 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_009 VALUES
  (1,'parallel_c_t009_r1',19621.77,DATE'2024-09-05'),
  (2,'parallel_c_t009_r2',43087.78,DATE'2024-06-06'),
  (3,'parallel_c_t009_r3',51605.78,DATE'2024-12-27'),
  (4,'parallel_c_t009_r4',39321.75,DATE'2024-06-17'),
  (5,'parallel_c_t009_r5',66909.68,DATE'2024-08-23'),
  (6,'parallel_c_t009_r6',73883.38,DATE'2024-08-27'),
  (7,'parallel_c_t009_r7',2280.47,DATE'2024-06-22'),
  (8,'parallel_c_t009_r8',14466.53,DATE'2024-10-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_010 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_010 VALUES
  (1,'parallel_c_t010_r1',90283.80,DATE'2024-01-20'),
  (2,'parallel_c_t010_r2',62305.33,DATE'2024-11-26'),
  (3,'parallel_c_t010_r3',75921.73,DATE'2024-04-24'),
  (4,'parallel_c_t010_r4',6831.74,DATE'2024-08-06'),
  (5,'parallel_c_t010_r5',68812.80,DATE'2024-12-20'),
  (6,'parallel_c_t010_r6',49932.18,DATE'2024-11-08'),
  (7,'parallel_c_t010_r7',4238.73,DATE'2024-12-04'),
  (8,'parallel_c_t010_r8',25106.02,DATE'2024-08-11'),
  (9,'parallel_c_t010_r9',54990.19,DATE'2024-07-23'),
  (10,'parallel_c_t010_r10',26824.52,DATE'2024-09-25');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_011 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_011 VALUES
  (1,'parallel_c_t011_r1',61914.94,DATE'2024-12-02'),
  (2,'parallel_c_t011_r2',92607.17,DATE'2024-09-07'),
  (3,'parallel_c_t011_r3',73616.41,DATE'2024-11-16'),
  (4,'parallel_c_t011_r4',68975.48,DATE'2024-06-06'),
  (5,'parallel_c_t011_r5',60349.68,DATE'2024-06-18'),
  (6,'parallel_c_t011_r6',46537.86,DATE'2024-12-22'),
  (7,'parallel_c_t011_r7',84419.88,DATE'2024-05-20'),
  (8,'parallel_c_t011_r8',63562.24,DATE'2024-04-09'),
  (9,'parallel_c_t011_r9',73249.38,DATE'2024-04-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_012 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_012 VALUES
  (1,'parallel_c_t012_r1',92492.26,DATE'2024-12-23'),
  (2,'parallel_c_t012_r2',64194.40,DATE'2024-08-12'),
  (3,'parallel_c_t012_r3',73564.92,DATE'2024-05-10'),
  (4,'parallel_c_t012_r4',16074.73,DATE'2024-11-18'),
  (5,'parallel_c_t012_r5',49867.50,DATE'2024-06-25'),
  (6,'parallel_c_t012_r6',19298.37,DATE'2024-01-10'),
  (7,'parallel_c_t012_r7',93703.10,DATE'2024-06-15');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_013 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_013 VALUES
  (1,'parallel_c_t013_r1',33712.95,DATE'2024-08-07'),
  (2,'parallel_c_t013_r2',26585.68,DATE'2024-05-18'),
  (3,'parallel_c_t013_r3',91348.34,DATE'2024-03-04'),
  (4,'parallel_c_t013_r4',80798.94,DATE'2024-10-08'),
  (5,'parallel_c_t013_r5',31879.06,DATE'2024-11-17'),
  (6,'parallel_c_t013_r6',29679.81,DATE'2024-04-02'),
  (7,'parallel_c_t013_r7',13233.52,DATE'2024-06-23'),
  (8,'parallel_c_t013_r8',62018.12,DATE'2024-11-25'),
  (9,'parallel_c_t013_r9',18118.00,DATE'2024-09-06'),
  (10,'parallel_c_t013_r10',53434.83,DATE'2024-08-16');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_014 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_014 VALUES
  (1,'parallel_c_t014_r1',26243.96,DATE'2024-05-11'),
  (2,'parallel_c_t014_r2',37509.82,DATE'2024-01-25'),
  (3,'parallel_c_t014_r3',11825.83,DATE'2024-10-08'),
  (4,'parallel_c_t014_r4',70243.94,DATE'2024-12-28'),
  (5,'parallel_c_t014_r5',4988.22,DATE'2024-07-27'),
  (6,'parallel_c_t014_r6',23140.04,DATE'2024-07-26'),
  (7,'parallel_c_t014_r7',65030.23,DATE'2024-12-28'),
  (8,'parallel_c_t014_r8',38041.04,DATE'2024-01-10'),
  (9,'parallel_c_t014_r9',74543.77,DATE'2024-02-11'),
  (10,'parallel_c_t014_r10',37398.58,DATE'2024-11-18');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_015 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_015 VALUES
  (1,'parallel_c_t015_r1',64838.17,DATE'2024-09-15'),
  (2,'parallel_c_t015_r2',35861.24,DATE'2024-02-11'),
  (3,'parallel_c_t015_r3',21398.93,DATE'2024-08-21'),
  (4,'parallel_c_t015_r4',33820.91,DATE'2024-03-01'),
  (5,'parallel_c_t015_r5',96649.43,DATE'2024-05-19'),
  (6,'parallel_c_t015_r6',88495.96,DATE'2024-04-06'),
  (7,'parallel_c_t015_r7',80179.81,DATE'2024-07-27'),
  (8,'parallel_c_t015_r8',56159.65,DATE'2024-06-03'),
  (9,'parallel_c_t015_r9',52638.85,DATE'2024-02-06');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_016 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_016 VALUES
  (1,'parallel_c_t016_r1',62680.41,DATE'2024-04-01'),
  (2,'parallel_c_t016_r2',34281.49,DATE'2024-04-15'),
  (3,'parallel_c_t016_r3',98896.34,DATE'2024-06-10'),
  (4,'parallel_c_t016_r4',76444.92,DATE'2024-10-01'),
  (5,'parallel_c_t016_r5',34382.83,DATE'2024-06-23'),
  (6,'parallel_c_t016_r6',31072.07,DATE'2024-11-04');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_017 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_017 VALUES
  (1,'parallel_c_t017_r1',40313.20,DATE'2024-07-22'),
  (2,'parallel_c_t017_r2',65966.90,DATE'2024-05-23'),
  (3,'parallel_c_t017_r3',15489.81,DATE'2024-05-12'),
  (4,'parallel_c_t017_r4',80660.28,DATE'2024-04-05'),
  (5,'parallel_c_t017_r5',62776.19,DATE'2024-08-24'),
  (6,'parallel_c_t017_r6',79495.47,DATE'2024-07-23'),
  (7,'parallel_c_t017_r7',72089.60,DATE'2024-09-26'),
  (8,'parallel_c_t017_r8',87144.27,DATE'2024-04-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_018 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_018 VALUES
  (1,'parallel_c_t018_r1',10830.67,DATE'2024-08-17'),
  (2,'parallel_c_t018_r2',92344.46,DATE'2024-02-19'),
  (3,'parallel_c_t018_r3',14807.07,DATE'2024-09-17'),
  (4,'parallel_c_t018_r4',26597.73,DATE'2024-09-05'),
  (5,'parallel_c_t018_r5',21666.41,DATE'2024-09-15'),
  (6,'parallel_c_t018_r6',15335.87,DATE'2024-04-23'),
  (7,'parallel_c_t018_r7',76534.62,DATE'2024-02-17'),
  (8,'parallel_c_t018_r8',58490.07,DATE'2024-08-05'),
  (9,'parallel_c_t018_r9',67372.53,DATE'2024-08-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_019 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_019 VALUES
  (1,'parallel_c_t019_r1',73342.59,DATE'2024-11-26'),
  (2,'parallel_c_t019_r2',40492.92,DATE'2024-01-13'),
  (3,'parallel_c_t019_r3',33452.00,DATE'2024-12-07'),
  (4,'parallel_c_t019_r4',75900.09,DATE'2024-01-14'),
  (5,'parallel_c_t019_r5',45258.89,DATE'2024-02-18');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_020 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_020 VALUES
  (1,'parallel_c_t020_r1',9146.60,DATE'2024-01-10'),
  (2,'parallel_c_t020_r2',53691.23,DATE'2024-03-25'),
  (3,'parallel_c_t020_r3',84185.93,DATE'2024-11-14'),
  (4,'parallel_c_t020_r4',49181.48,DATE'2024-08-28'),
  (5,'parallel_c_t020_r5',49579.48,DATE'2024-02-22');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_021 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_021 VALUES
  (1,'parallel_c_t021_r1',70787.17,DATE'2024-11-28'),
  (2,'parallel_c_t021_r2',45682.15,DATE'2024-03-18'),
  (3,'parallel_c_t021_r3',51618.67,DATE'2024-03-24'),
  (4,'parallel_c_t021_r4',29288.00,DATE'2024-01-10'),
  (5,'parallel_c_t021_r5',60786.86,DATE'2024-12-18'),
  (6,'parallel_c_t021_r6',55691.68,DATE'2024-07-27'),
  (7,'parallel_c_t021_r7',30202.31,DATE'2024-08-12'),
  (8,'parallel_c_t021_r8',20432.35,DATE'2024-04-24'),
  (9,'parallel_c_t021_r9',14890.04,DATE'2024-11-14'),
  (10,'parallel_c_t021_r10',80699.98,DATE'2024-01-08');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_022 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_022 VALUES
  (1,'parallel_c_t022_r1',8919.12,DATE'2024-10-02'),
  (2,'parallel_c_t022_r2',58615.76,DATE'2024-11-23'),
  (3,'parallel_c_t022_r3',6480.31,DATE'2024-12-02'),
  (4,'parallel_c_t022_r4',52825.56,DATE'2024-04-18'),
  (5,'parallel_c_t022_r5',28577.96,DATE'2024-01-05'),
  (6,'parallel_c_t022_r6',66146.37,DATE'2024-04-27');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_023 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_023 VALUES
  (1,'parallel_c_t023_r1',75611.40,DATE'2024-10-20'),
  (2,'parallel_c_t023_r2',88250.41,DATE'2024-04-10'),
  (3,'parallel_c_t023_r3',18877.84,DATE'2024-09-08'),
  (4,'parallel_c_t023_r4',54306.38,DATE'2024-05-02'),
  (5,'parallel_c_t023_r5',73070.75,DATE'2024-12-06'),
  (6,'parallel_c_t023_r6',82150.86,DATE'2024-07-18'),
  (7,'parallel_c_t023_r7',65062.06,DATE'2024-06-21'),
  (8,'parallel_c_t023_r8',88094.48,DATE'2024-09-11'),
  (9,'parallel_c_t023_r9',91366.53,DATE'2024-07-05'),
  (10,'parallel_c_t023_r10',39406.48,DATE'2024-03-25');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_024 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_024 VALUES
  (1,'parallel_c_t024_r1',62165.30,DATE'2024-04-10'),
  (2,'parallel_c_t024_r2',92843.18,DATE'2024-08-02'),
  (3,'parallel_c_t024_r3',73813.52,DATE'2024-07-18'),
  (4,'parallel_c_t024_r4',69567.17,DATE'2024-07-08'),
  (5,'parallel_c_t024_r5',33522.26,DATE'2024-06-21'),
  (6,'parallel_c_t024_r6',10463.57,DATE'2024-06-03'),
  (7,'parallel_c_t024_r7',70306.92,DATE'2024-04-02'),
  (8,'parallel_c_t024_r8',35287.48,DATE'2024-11-20'),
  (9,'parallel_c_t024_r9',79126.05,DATE'2024-02-07');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_025 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_025 VALUES
  (1,'parallel_c_t025_r1',95036.85,DATE'2024-09-07'),
  (2,'parallel_c_t025_r2',62935.26,DATE'2024-06-10'),
  (3,'parallel_c_t025_r3',2109.27,DATE'2024-04-24'),
  (4,'parallel_c_t025_r4',15476.95,DATE'2024-08-08'),
  (5,'parallel_c_t025_r5',91277.77,DATE'2024-12-07'),
  (6,'parallel_c_t025_r6',52087.30,DATE'2024-09-11'),
  (7,'parallel_c_t025_r7',37201.48,DATE'2024-08-18'),
  (8,'parallel_c_t025_r8',85129.45,DATE'2024-05-09'),
  (9,'parallel_c_t025_r9',47239.65,DATE'2024-08-15');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_026 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_026 VALUES
  (1,'parallel_c_t026_r1',94788.60,DATE'2024-06-07'),
  (2,'parallel_c_t026_r2',48689.40,DATE'2024-07-02'),
  (3,'parallel_c_t026_r3',73831.28,DATE'2024-12-05'),
  (4,'parallel_c_t026_r4',2225.33,DATE'2024-09-19'),
  (5,'parallel_c_t026_r5',76009.92,DATE'2024-07-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_027 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_027 VALUES
  (1,'parallel_c_t027_r1',25815.42,DATE'2024-04-13'),
  (2,'parallel_c_t027_r2',74783.31,DATE'2024-08-18'),
  (3,'parallel_c_t027_r3',85900.87,DATE'2024-06-09'),
  (4,'parallel_c_t027_r4',64144.92,DATE'2024-11-24'),
  (5,'parallel_c_t027_r5',64403.58,DATE'2024-03-24'),
  (6,'parallel_c_t027_r6',46260.21,DATE'2024-03-24');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_028 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_028 VALUES
  (1,'parallel_c_t028_r1',64104.23,DATE'2024-09-21'),
  (2,'parallel_c_t028_r2',7798.67,DATE'2024-01-27'),
  (3,'parallel_c_t028_r3',9842.85,DATE'2024-01-25'),
  (4,'parallel_c_t028_r4',936.52,DATE'2024-03-27'),
  (5,'parallel_c_t028_r5',82767.29,DATE'2024-02-23'),
  (6,'parallel_c_t028_r6',19895.01,DATE'2024-04-17'),
  (7,'parallel_c_t028_r7',59870.47,DATE'2024-01-20'),
  (8,'parallel_c_t028_r8',83688.85,DATE'2024-10-16'),
  (9,'parallel_c_t028_r9',86513.62,DATE'2024-01-01');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_029 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_029 VALUES
  (1,'parallel_c_t029_r1',72405.52,DATE'2024-01-01'),
  (2,'parallel_c_t029_r2',69491.92,DATE'2024-05-18'),
  (3,'parallel_c_t029_r3',37676.02,DATE'2024-09-27'),
  (4,'parallel_c_t029_r4',91569.86,DATE'2024-07-26'),
  (5,'parallel_c_t029_r5',23596.13,DATE'2024-02-17'),
  (6,'parallel_c_t029_r6',19616.30,DATE'2024-04-20'),
  (7,'parallel_c_t029_r7',69103.32,DATE'2024-06-09'),
  (8,'parallel_c_t029_r8',52122.10,DATE'2024-06-13'),
  (9,'parallel_c_t029_r9',60261.72,DATE'2024-04-23');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_030 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_030 VALUES
  (1,'parallel_c_t030_r1',39418.87,DATE'2024-02-21'),
  (2,'parallel_c_t030_r2',85282.97,DATE'2024-01-03'),
  (3,'parallel_c_t030_r3',53220.48,DATE'2024-07-18'),
  (4,'parallel_c_t030_r4',62490.07,DATE'2024-11-01'),
  (5,'parallel_c_t030_r5',92223.21,DATE'2024-02-16'),
  (6,'parallel_c_t030_r6',56973.82,DATE'2024-06-19');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_031 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_031 VALUES
  (1,'parallel_c_t031_r1',69326.05,DATE'2024-04-07'),
  (2,'parallel_c_t031_r2',90839.72,DATE'2024-08-09'),
  (3,'parallel_c_t031_r3',6202.09,DATE'2024-11-09'),
  (4,'parallel_c_t031_r4',71276.72,DATE'2024-11-02'),
  (5,'parallel_c_t031_r5',23608.40,DATE'2024-01-07');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_032 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_032 VALUES
  (1,'parallel_c_t032_r1',18999.96,DATE'2024-12-27'),
  (2,'parallel_c_t032_r2',52305.09,DATE'2024-05-06'),
  (3,'parallel_c_t032_r3',74019.30,DATE'2024-10-27'),
  (4,'parallel_c_t032_r4',51107.86,DATE'2024-09-11'),
  (5,'parallel_c_t032_r5',50411.96,DATE'2024-12-05'),
  (6,'parallel_c_t032_r6',90449.92,DATE'2024-02-17'),
  (7,'parallel_c_t032_r7',97808.44,DATE'2024-01-04'),
  (8,'parallel_c_t032_r8',57390.29,DATE'2024-02-11'),
  (9,'parallel_c_t032_r9',79420.98,DATE'2024-10-20');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_033 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_033 VALUES
  (1,'parallel_c_t033_r1',42878.03,DATE'2024-11-09'),
  (2,'parallel_c_t033_r2',59154.62,DATE'2024-04-12'),
  (3,'parallel_c_t033_r3',72587.48,DATE'2024-07-06'),
  (4,'parallel_c_t033_r4',89188.74,DATE'2024-11-13'),
  (5,'parallel_c_t033_r5',11332.98,DATE'2024-10-10'),
  (6,'parallel_c_t033_r6',32402.91,DATE'2024-02-03'),
  (7,'parallel_c_t033_r7',35223.19,DATE'2024-07-23'),
  (8,'parallel_c_t033_r8',83186.19,DATE'2024-12-13');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_034 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_034 VALUES
  (1,'parallel_c_t034_r1',47377.13,DATE'2024-02-01'),
  (2,'parallel_c_t034_r2',40505.56,DATE'2024-06-25'),
  (3,'parallel_c_t034_r3',35458.13,DATE'2024-03-03'),
  (4,'parallel_c_t034_r4',24661.55,DATE'2024-08-18'),
  (5,'parallel_c_t034_r5',72810.65,DATE'2024-07-04'),
  (6,'parallel_c_t034_r6',3542.11,DATE'2024-06-18'),
  (7,'parallel_c_t034_r7',12298.76,DATE'2024-10-26');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_035 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_035 VALUES
  (1,'parallel_c_t035_r1',50593.01,DATE'2024-05-14'),
  (2,'parallel_c_t035_r2',50929.99,DATE'2024-02-24'),
  (3,'parallel_c_t035_r3',73439.31,DATE'2024-10-17'),
  (4,'parallel_c_t035_r4',22327.87,DATE'2024-07-06'),
  (5,'parallel_c_t035_r5',18298.34,DATE'2024-05-09'),
  (6,'parallel_c_t035_r6',64734.18,DATE'2024-02-06'),
  (7,'parallel_c_t035_r7',57073.35,DATE'2024-07-10');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_036 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_036 VALUES
  (1,'parallel_c_t036_r1',10182.46,DATE'2024-05-08'),
  (2,'parallel_c_t036_r2',94589.80,DATE'2024-08-20'),
  (3,'parallel_c_t036_r3',80928.25,DATE'2024-08-04'),
  (4,'parallel_c_t036_r4',17858.38,DATE'2024-01-13'),
  (5,'parallel_c_t036_r5',43644.79,DATE'2024-07-26'),
  (6,'parallel_c_t036_r6',43293.56,DATE'2024-06-14'),
  (7,'parallel_c_t036_r7',85432.76,DATE'2024-03-10'),
  (8,'parallel_c_t036_r8',42130.77,DATE'2024-12-07');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_037 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_037 VALUES
  (1,'parallel_c_t037_r1',41280.22,DATE'2024-07-11'),
  (2,'parallel_c_t037_r2',38300.94,DATE'2024-12-21'),
  (3,'parallel_c_t037_r3',64457.73,DATE'2024-04-11'),
  (4,'parallel_c_t037_r4',49390.35,DATE'2024-07-12'),
  (5,'parallel_c_t037_r5',15027.72,DATE'2024-04-19'),
  (6,'parallel_c_t037_r6',71616.23,DATE'2024-11-25'),
  (7,'parallel_c_t037_r7',72202.03,DATE'2024-12-15'),
  (8,'parallel_c_t037_r8',92947.26,DATE'2024-08-26');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_038 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_038 VALUES
  (1,'parallel_c_t038_r1',90911.08,DATE'2024-07-22'),
  (2,'parallel_c_t038_r2',65544.17,DATE'2024-11-10'),
  (3,'parallel_c_t038_r3',31822.32,DATE'2024-11-05'),
  (4,'parallel_c_t038_r4',93847.54,DATE'2024-07-03'),
  (5,'parallel_c_t038_r5',58960.76,DATE'2024-08-19'),
  (6,'parallel_c_t038_r6',52634.68,DATE'2024-09-28'),
  (7,'parallel_c_t038_r7',90763.53,DATE'2024-09-02');

CREATE TABLE IF NOT EXISTS ai27_ucsync_testcatalog.parallel_c.t_039 (
  id INT NOT NULL COMMENT 'row id',
  name STRING COMMENT 'label',
  amount DECIMAL(12,2) COMMENT 'value',
  created_at DATE COMMENT 'created')
USING DELTA TBLPROPERTIES ('ai27_uc.fixture'='true', 'ai27_uc.parallel'='true');
INSERT INTO ai27_ucsync_testcatalog.parallel_c.t_039 VALUES
  (1,'parallel_c_t039_r1',92064.68,DATE'2024-10-21'),
  (2,'parallel_c_t039_r2',11238.13,DATE'2024-04-22'),
  (3,'parallel_c_t039_r3',87222.45,DATE'2024-03-21'),
  (4,'parallel_c_t039_r4',80404.05,DATE'2024-10-21'),
  (5,'parallel_c_t039_r5',88897.82,DATE'2024-07-25'),
  (6,'parallel_c_t039_r6',43563.55,DATE'2024-02-01'),
  (7,'parallel_c_t039_r7',13024.33,DATE'2024-04-17');

