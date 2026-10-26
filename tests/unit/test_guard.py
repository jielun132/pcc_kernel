import pytest

from pcc_tool.capture.guard import dependencies, guard_sql, validate_session
from pcc_tool.models import PCCError


@pytest.mark.parametrize("sql,kind", [
    ("SELECT /*+ HASH_JOIN(a,b) */ * FROM a JOIN b ON a.id=b.id", "select"),
    ("WITH c AS (SELECT * FROM app.t) SELECT * FROM c", "select"),
    ("SELECT 1 UNION ALL SELECT 2", "select"),
    ("SELECT * FROM t WHERE id IN (SELECT id FROM t2)", "select"),
    ("UPDATE t SET value=2 WHERE id=1", "update"),
    ("DELETE FROM t WHERE id=1", "delete"),
    ("SELECT 'a;?b', '?' FROM app.t; -- comment", "select"),
    ("WITH c AS (SELECT id FROM t2) UPDATE t SET id=1 WHERE id IN (SELECT id FROM c)", "update"),
])
def test_supported(sql, kind):
    assert guard_sql(sql)[1] == kind


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE t", "EXPLAIN ANALYZE SELECT 1", "ANALYZE TABLE t", "SET x=1", "SHOW TABLES",
    "SELECT * FROM t INTO OUTFILE '/tmp/out'", "SELECT @a:=1", "SELECT * FROM t FOR UPDATE",
    "SELECT SLEEP(10)", "SELECT GET_LOCK('x',1)", "SELECT BENCHMARK(100,1)", "SELECT LOAD_FILE('/tmp/x')",
    "SELECT NEXTVAL(s)", "SELECT 1 /*!; DROP TABLE t*/", "SELECT /*T![x] 1 */ 1", "SELECT * FROM",
    "CALL p()", "DROP TABLE t", "SELECT (1", "INSERT INTO t VALUES (1)", "REPLACE INTO t VALUES (1)",
])
def test_rejected(sql):
    with pytest.raises(PCCError):
        guard_sql(sql)


@pytest.mark.parametrize("sql", [None, "", "EXECUTE stmt USING @p", "SELECT * FROM t WHERE id=?", "SELECT @p"])
def test_needs_sample(sql):
    with pytest.raises(PCCError) as err:
        guard_sql(sql)
    assert err.value.code == "NEEDS_EXECUTABLE_SAMPLE"


@pytest.mark.parametrize("sql,schema,expected", [
    ("WITH c AS (SELECT * FROM app.t) SELECT * FROM c", "", [["app", "t"]]),
    ("SELECT * FROM app.t AS a JOIN app.t AS b ON a.id=b.id", "", [["app", "t"]]),
    ("SELECT * FROM app.t UNION SELECT * FROM other.u", "", [["app", "t"], ["other", "u"]]),
    ("UPDATE t SET x=(SELECT max(x) FROM u)", "app", [["app", "t"], ["app", "u"]]),
    ("DELETE a FROM app.t a JOIN app.u b ON a.id=b.id", "", [["app", "t"], ["app", "u"]]),
])
def test_physical_dependencies(sql, schema, expected):
    assert dependencies(guard_sql(sql)[0], schema) == expected


def test_session_whitelist():
    assert validate_session({"tidb_allow_mpp": "ON", "time_zone": "+08:00"}) == {"tidb_allow_mpp": 1, "time_zone": "+08:00"}
    for value in ({"autocommit": 0}, {"tidb_allow_mpp": "1;DROP TABLE t"}, {"sql_mode": "ANSI_QUOTES"},
                  {"tidb_stats_load_sync_wait": 30001}, {"tidb_isolation_read_engines": "anything"}):
        with pytest.raises(PCCError):
            validate_session(value)


def test_version_comment_text_in_literal_is_valid():
    assert guard_sql("SELECT '/*! version text */', 'a/*b'")[1] == "select"


def test_hint_cannot_set_arbitrary_session_variables():
    with pytest.raises(PCCError):
        guard_sql("SELECT /*+ SET_VAR(max_execution_time=0) */ 1")


def test_explicit_allowed_list_cannot_enable_ddl():
    with pytest.raises(PCCError):
        guard_sql("CREATE TABLE t (id INT)", allowed=("create",))
