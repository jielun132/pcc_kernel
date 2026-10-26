import json

import pytest

from pcc_tool import api
from pcc_tool.artifacts.store import load_bundle, write_jsonl
from pcc_tool.capture.explain import capture_plan
from pcc_tool.capture.guard import guard_sql
from pcc_tool.capture.parameters import parameter_bindings
from pcc_tool.collector.summary import make_query
from pcc_tool.models import ExportRequest, PCCError


@pytest.mark.parametrize("kind,value", [
    ("null", None), ("bool", True), ("int", "-9223372036854775808"),
    ("uint", "18446744073709551615"), ("decimal", "12345678901234567890.012345678901234567890123456789"),
    ("decimal", "0.000000000000000000000000000001"), ("double", "1.23e-100"),
    ("string", "中文😀';?,%s\\\n/*+ */"), ("bytes", "00ff80"), ("date", "2024-02-29"),
    ("datetime", "2024-02-29 23:59:59.123456"), ("time", "-838:59:59.123456"),
    ("json", '{"a": [1,null,"?,;"]}'),
])
def test_explicit_parameter_types(kind, value):
    params = [{"type": kind, "value": value}]
    sql = "SELECT /*+ QB_NAME(main) */ ? AS v, '?' AS question -- ?\n"
    tree, statement_type = guard_sql(sql, parameters=params)
    assert statement_type == "select"
    assert len(parameter_bindings(params)) == 1
    assert make_query(sql, parameters=params).executable_status == "ok"


@pytest.mark.parametrize("parameter", [
    {"type": "int", "value": "9223372036854775808"}, {"type": "int", "value": "1; SELECT 1"},
    {"type": "uint", "value": "-1"}, {"type": "uint", "value": "18446744073709551616"},
    {"type": "int", "value": True}, {"type": "decimal", "value": "1e3"},
    {"type": "decimal", "value": "1." + "1" * 31}, {"type": "double", "value": "NaN"},
    {"type": "double", "value": "1e999"}, {"type": "string", "value": None},
    {"type": "bytes", "value": "0f0"}, {"type": "bytes", "value": "zz"},
    {"type": "bool", "value": "false"}, {"type": "null", "value": "NULL"},
    {"type": "date", "value": "2023-02-29"}, {"type": "datetime", "value": "2024-01-01T12:00:00Z"},
    {"type": "time", "value": "839:00:00"}, {"type": "time", "value": "12:60:00"},
    {"type": "json", "value": "{broken}"}, {"type": "json", "value": "NaN"},
    {"type": "sql", "value": "NOW()"}, {"type": "string", "value": "x", "extra": 1},
    {"type": [], "value": "7"},
])
def test_invalid_parameter_is_not_silently_coerced(parameter):
    q = make_query("SELECT ?", parameters=[parameter])
    assert q.executable_status == "unsupported"
    assert q.issues[0]["code"] == "INVALID_PARAMETERS"


@pytest.mark.parametrize("sql,params", [
    ("SELECT ?,?", [{"type": "int", "value": "1"}]), ("SELECT ?", []),
    ("SELECT '?'", [{"type": "int", "value": "1"}]),
    ("EXECUTE app_stmt USING @p", []), ("PREPARE stmt FROM 'SELECT 1'", []),
    ("SELECT @p", []), ("SELECT :named", [{"type": "int", "value": "1"}]),
    ("/* source */ EXECUTE old_stmt USING @p", None),
])
def test_missing_values_or_session_state_need_sample(sql, params):
    assert make_query(sql, parameters=params).executable_status == "needs_sample"


def test_parameter_values_and_types_are_part_of_identity_and_bundle(tmp_path):
    path = tmp_path / "samples.jsonl"
    rows = [{"sql_text": "SELECT ?", "parameters": [{"type": kind, "value": value}]}
            for kind, value in [("int", "7"), ("int", "8"), ("string", "7")]]
    write_jsonl(path, rows)
    bundle = api.export(ExportRequest(str(tmp_path / "bundle"), sql_file=str(path)))
    loaded = load_bundle(bundle.path)
    assert len({q.query_id for q in loaded.queries}) == 3
    assert [q.parameters for q in loaded.queries] == [r["parameters"] for r in rows]
    assert len({q.sql_hash for q in loaded.queries}) == 1


def test_execute_only_runs_server_prepared_explain_and_retains_warnings():
    from test_capture import FakeDB

    class PreparedDB(FakeDB):
        calls = []
        def query(self, sql, params=()):
            if sql.startswith("EXECUTE"):
                self.calls.append((sql, params))
                return ["id", "estRows", "task", "access object", "operator info"], [["Update_1", "N/A", "root", "", "N/A"]]
            return super().query(sql, params)

    sql = "UPDATE /*+ USE_INDEX(t,PRIMARY) */ t SET v=? WHERE id=?"
    params = [{"type": "string", "value": "'; DROP TABLE t; -- ?"}, {"type": "int", "value": "7"}]
    snap = capture_plan(make_query(sql, "app", parameters=params), "old", {"host": "fake"}, db_factory=PreparedDB)
    assert snap.capture_status == "ok", snap.issues
    calls = PreparedDB.calls
    prepare = next(c for c in calls if c[0].startswith("PREPARE"))
    assert prepare == ("PREPARE pcc_explain FROM %s", ("EXPLAIN FORMAT='row' " + sql,))
    execute = next(i for i, c in enumerate(calls) if c[0].startswith("EXECUTE"))
    assert calls[execute][0] == "EXECUTE pcc_explain USING @pcc_arg_0,@pcc_arg_1"
    assert calls[execute + 1][0] == "SHOW WARNINGS"
    assert not any(c[0].startswith(("UPDATE", "DELETE", "INSERT", "REPLACE")) for c in calls)
    assert all(params[0]["value"] not in c[0] for c in calls)
    assert snap.context["prepared_plan_cache"] == "not_reproduced"


def test_binary_plan_preserves_bytes_and_does_not_equal_literal_escape(tmp_path):
    from pcc_tool.capture.connection import clean_cell
    from pcc_tool.artifacts.store import write_json, read_json
    from pcc_tool.models import canonical, PlanSnapshot
    from pcc_tool.plan.parser import parse_plan
    from pcc_tool.plan.diff import compare_ir
    from pcc_tool.render.report import escaped

    raw = b'range:["\xff\x00","\xff\x00"], keep order:false'
    value = clean_cell(raw)
    assert value.encode("utf-8", "surrogateescape") == raw
    write_json(tmp_path / "binary.json", {"value":value})
    assert read_json(tmp_path / "binary.json")["value"] == value
    assert json.loads(canonical(value)) == value
    assert "\\udcff" in escaped(value)
    cols = ["id","estRows","task","access object","operator info"]
    def ir(text):
        return parse_plan(PlanSnapshot("q","old",columns=cols,rows=[["IndexRangeScan_1","1","cop[tikv]","table:t, index:i(b)",text]]))
    assert ir(value).parse_status == "ok"
    assert not compare_ir(ir(value),ir(value))[0]
    assert compare_ir(ir(value),ir(raw.decode("utf-8","backslashreplace")))[0]


@pytest.mark.parametrize("sql", [
    "UPDATE /*+ USE_INDEX(t,PRIMARY) */ t SET v=1 WHERE id=7; DELETE FROM t",
    "DELETE FROM t ORDER BY id LIMIT 1; DROP TABLE t",
    "REPLACE INTO t VALUES(1); DROP TABLE t",
    "REPLACE INTO t VALUES(SLEEP(1))",
    "SELECT /*+ SET_VAR(max_execution_time=0) */ ?",
])
def test_dialect_extensions_do_not_bypass_guard(sql):
    with pytest.raises(PCCError):
        guard_sql(sql, ("select","update","delete","insert","replace"), parameters=[{"type":"int","value":"1"}])


def test_cancel_during_binding_never_executes():
    import threading
    from test_capture import FakeDB
    cancel = threading.Event()
    class BindingDB(FakeDB):
        calls = []
        def query(self, sql, params=()):
            if sql.startswith("SET @pcc_arg_0"):
                cancel.set()
            return super().query(sql, params)
    q = make_query("SELECT ?,?", parameters=[{"type":"int","value":"1"}]*2)
    result = capture_plan(q,"old",{"host":"fake"},db_factory=BindingDB,cancel=cancel)
    assert result.capture_status == "failed"
    assert any(i["code"] == "CANCELLED" for i in result.issues)
    assert not any(sql.startswith(("SET @pcc_arg_1", "EXECUTE")) for sql,_ in BindingDB.calls)


def test_binding_warning_never_executes():
    from test_capture import FakeDB
    class CoercionDB(FakeDB):
        calls = []
        def records(self, sql, params=()):
            warning = sql == "SHOW WARNINGS" and self.calls[-1][0].startswith("SET @pcc_arg")
            super().records(sql,params)
            return [{"Code":"1292","Message":"truncated value"}] if warning else []
    result = capture_plan(make_query("SELECT ?",parameters=[{"type":"string","value":"1"}]),"old",{"host":"fake"},db_factory=CoercionDB)
    assert result.capture_status == "failed"
    assert result.issues[0]["code"] == "PARAMETER_COERCION"
    assert not any(sql.startswith("EXECUTE") for sql,_ in CoercionDB.calls)


def test_packaged_schema_includes_prepared_contract():
    import dataclasses
    import importlib.resources
    from pcc_tool.models import QueryCase, DoctorRequest
    schema = json.loads(importlib.resources.files("pcc_tool").joinpath("schemas/contracts-v1.schema.json").read_text())
    properties = schema["$defs"]["QueryCase"]["properties"]
    assert set(properties) == {field.name for field in dataclasses.fields(QueryCase)}
    assert "decimal" in properties["parameters"]["items"]["properties"]["type"]["enum"]
    result = api.doctor(DoctorRequest())
    assert result["run_status"] == "completed", result["issues"]
