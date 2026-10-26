import pytest
from pcc_tool.capture.guard import dependencies, guard_sql
from pcc_tool.models import PCCError


@pytest.mark.parametrize("sql,kind,tables", [
    ("INSERT INTO t SET id=1,v='?,; int(11)'", "insert", [["app","t"]]),
    ("REPLACE /*+ MEMORY_QUOTA(64 MB) */ INTO t SET id=1,v=(SELECT MAX(v) FROM s)", "replace", [["app","s"],["app","t"]]),
    ("INSERT INTO t SET id=1,v=2 ON DUPLICATE KEY UPDATE v=VALUES(v)", "insert", [["app","t"]]),
    ("UPDATE /*+ USE_INDEX(t,PRIMARY) */ IGNORE t SET v=1 WHERE id=2", "update", [["app","t"]]),
])
def test_common_dml_variants_validate_full_values_and_dependencies(sql, kind, tables):
    tree, actual = guard_sql(sql, ("insert","replace","update"))
    assert actual == kind and dependencies(tree, "app") == tables


@pytest.mark.parametrize("sql", [
    "INSERT INTO t SET id=1; DELETE FROM t",
    "INSERT INTO t SET id=SLEEP(10)",
    "REPLACE INTO t SET id=1,v=(@x:=2)",
    "UPDATE IGNORE t SET v=LOAD_FILE('/tmp/x') WHERE id=1",
    "INSERT INTO t SET id=", "INSERT INTO t SET 1=2",
])
def test_dml_validation_cannot_hide_unsafe_or_incomplete_expressions(sql):
    with pytest.raises(PCCError):
        guard_sql(sql, ("insert","replace","update"))


def test_insert_set_prepared_parameters_are_counted():
    sql = "INSERT INTO t SET id=?,v=?"
    params = [{"type":"int","value":"1"},{"type":"string","value":"?,;"}]
    assert guard_sql(sql,("insert",),parameters=params)[1] == "insert"
    with pytest.raises(PCCError):
        guard_sql(sql,("insert",),parameters=params[:1])
