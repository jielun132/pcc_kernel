"""Independent binary protocol oracle; connector is a test-only optional dependency."""
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from sql_matrix import ALLOWED, CASES, SCHEMA
from pcc_tool.artifacts.store import write_json
from pcc_tool.capture.connection import clean_cell, load_profiles
from pcc_tool.capture.explain import capture_plan
from pcc_tool.collector.summary import make_query
from pcc_tool.models import PlanSnapshot
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("PCC_RUN_PREPARED_PROTOCOL") != "1", reason="Binary protocol oracle is explicitly opt-in")]
PROTOCOL_CASES = [c for c in CASES if c["parameters"] is not None] + [
    {"name":"negative_time_as_application_text", "sql_text":"SELECT id FROM matrix_types WHERE duration=?",
     "parameters":[{"type":"string","value":"-12:34:56.123456"}]},
    {"name":"json_as_application_text", "sql_text":"SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)",
     "parameters":[{"type":"string","value":'{"n":3}'}]},
    {"name":"positive_binary_time", "sql_text":"SELECT id FROM matrix_types WHERE duration=?",
     "parameters":[{"type":"time","value":"12:34:56.123456"}]},
]


def native_value(parameter):
    kind, value = parameter["type"], parameter["value"]
    if kind in {"int","uint"}:
        return int(value)
    if kind == "decimal":
        return Decimal(value)
    if kind == "double":
        return float(value)
    if kind == "bytes":
        return bytes.fromhex(value)
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "time":
        h,m,s = value.lstrip("-").split(":")
        seconds, _, micros = s.partition(".")
        duration = timedelta(hours=int(h),minutes=int(m),seconds=int(seconds),microseconds=int(micros.ljust(6,"0")))
        return -duration if value.startswith("-") else duration
    return value


@pytest.mark.parametrize("case", PROTOCOL_CASES, ids=lambda c:c["name"])
def test_sql_prepare_matches_binary_protocol(case):
    # Import failure when opted in is a real failure, not an implicit skip.
    import mysql.connector

    profiles = load_profiles(os.environ["PCC_TEST_CONFIG"])
    profile = profiles["old"]
    q = make_query(case["sql_text"],SCHEMA,parameters=case["parameters"],allowed=ALLOWED)
    sql_snapshot = capture_plan(q,"old",profile,format="tidb_json")
    assert sql_snapshot.capture_status == "ok", sql_snapshot.issues
    connection = mysql.connector.connect(host=profile["host"],port=int(profile.get("port",4000)),
        user=profile["user"],password=os.environ[profile["password_env"]],database=SCHEMA,
        charset="utf8mb4",collation="utf8mb4_bin",use_pure=True,use_unicode=False,
        connection_timeout=5,autocommit=True)
    try:
        with connection.cursor() as control:
            control.execute("SET SESSION tidb_use_plan_baselines=0")
            control.execute("SELECT TIDB_VERSION()")
            assert profile["expected_version"].encode() in bytes(control.fetchone()[0])
        with connection.cursor(prepared=True) as cursor:
            assert cursor.__class__.__name__ == "MySQLCursorPrepared"
            cursor.execute("EXPLAIN FORMAT='tidb_json' " + case["sql_text"],tuple(native_value(p) for p in case["parameters"]))
            rows = [[clean_cell(bytes(v) if isinstance(v,bytearray) else v) for v in row] for row in cursor.fetchall()]
            columns = [str(d[0]) for d in cursor.description]
        oracle = PlanSnapshot(q.query_id,"new",version=sql_snapshot.version,format="tidb_json",columns=columns,rows=rows)
        left, right = parse_plan(sql_snapshot),parse_plan(oracle)
        events, review = compare_ir(left,right)
        write_json(Path(os.environ["PCC_LIVE_OUTPUT"]) / "binary-protocol" / (case["name"]+".json"),
                   {"query":q,"sql_prepare":sql_snapshot,"binary_prepare":oracle,"diffs":events,"requires_review":review,
                    "connector_version":mysql.connector.__version__,"protocol":"COM_STMT_PREPARE/COM_STMT_EXECUTE"})
        assert left.parse_status == right.parse_status == "ok"
        assert not events and not review, events
    finally:
        connection.close()


def test_binary_send_long_data():
    import io
    import mysql.connector
    profiles = load_profiles(os.environ["PCC_TEST_CONFIG"])
    profile = profiles["old"]
    value = ("中文?,;'😀" * 8192).encode()
    connection = mysql.connector.connect(host=profile["host"],port=int(profile.get("port",4000)),
        user=profile["user"],password=os.environ[profile["password_env"]],database=SCHEMA,
        charset="utf8mb4",use_pure=True,use_unicode=False,connection_timeout=5,autocommit=True)
    try:
        with connection.cursor(prepared=True) as cursor:
            cursor.execute("EXPLAIN FORMAT='tidb_json' SELECT id FROM matrix_types WHERE text_value=?", (io.BytesIO(value),))
            rows = [[clean_cell(bytes(v) if isinstance(v,bytearray) else v) for v in row] for row in cursor.fetchall()]
            snapshot = PlanSnapshot("long-data","probe",format="tidb_json",columns=[str(d[0]) for d in cursor.description],rows=rows)
            assert parse_plan(snapshot).parse_status == "ok"
            write_json(Path(os.environ["PCC_LIVE_OUTPUT"]) / "binary-protocol/long-data.json", {"bytes":len(value),"snapshot":snapshot,"protocol":"COM_STMT_SEND_LONG_DATA"})
    finally:
        connection.close()
