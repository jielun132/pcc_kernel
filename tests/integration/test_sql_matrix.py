"""Run only against the explicitly approved isolated fixture schema."""
import dataclasses
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from sql_matrix import ALLOWED, CASES, SCHEMA
from pcc_tool import api
from pcc_tool.artifacts.store import load_bundle, verify, write_json, write_jsonl
from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.capture.explain import capture_plan
from pcc_tool.collector.summary import make_query
from pcc_tool.models import CompareRequest, DiffRequest, ExportRequest, digest
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("PCC_RUN_SQL_MATRIX") != "1", reason="SQL matrix live test is explicitly opt-in")]


def data_fingerprint(db):
    return {table: db.query(f"SELECT COUNT(*),SUM(id),BIT_XOR(CRC32(CONCAT_WS('|',{columns}))) FROM `{SCHEMA}`.`{table}`")[1]
            for table, columns in {"orders":"id,customer_id,amount,status,note", "customers":"id,region,name",
                                   "items":"id,order_id,category,price", "matrix_types":"id,text_value,money,doc"}.items()}


@pytest.fixture(scope="module")
def live():
    profiles = load_profiles(os.environ["PCC_TEST_CONFIG"])
    assert all(profiles[k].get("expected_version") for k in ("source", "old", "new"))
    root = Path(os.environ["PCC_LIVE_OUTPUT"])
    root.mkdir(parents=True, exist_ok=True)
    with Database(profiles["source"]) as db:
        version = db.scalar("SELECT TIDB_VERSION()")
        assert db.scalar(f"SELECT COUNT(*) FROM `{SCHEMA}`.matrix_types") == "32"
        before = data_fingerprint(db)
    write_json(root / "environment.json", {"version":version,"cases":len(CASES),"categories":dict(Counter(c["category"] for c in CASES)),"scope":"same_cluster_two_nodes_not_cross_version"})
    yield profiles, root
    with Database(profiles["source"]) as db:
        after = data_fingerprint(db)
    write_json(root / "data-unchanged.json", {"before":before,"after":after})
    assert before == after, "EXPLAIN, including prepared DML, must preserve fixture data"


@pytest.mark.parametrize("fmt", ["row", "tidb_json"])
@pytest.mark.parametrize("case", CASES, ids=lambda c:c["name"])
def test_real_matrix_capture_parse_compare(live, fmt, case):
    profiles, root = live
    q = make_query(case["sql_text"], SCHEMA, allowed=ALLOWED, parameters=case["parameters"])
    snapshots, plans = [], []
    for side in ("old", "new"):
        snapshot = capture_plan(q, side, profiles[side], format=fmt, stats_retries=1)
        ir = parse_plan(snapshot)
        snapshots.append(snapshot)
        plans.append(ir)
    write_json(root / "goldens" / f"{case['name']}-{fmt}.json", {"case":case,"query":q,"snapshots":snapshots,"plans":plans})
    for snapshot, ir in zip(snapshots,plans):
        assert snapshot.capture_status == "ok", snapshot.issues
        assert not snapshot.issues, snapshot.issues
        assert ir.parse_status == "ok", ir.issues
    events, review = compare_ir(*plans)
    assert not events and not review, events
    if case["name"] == "hint_invalid_index":
        assert all(s.warnings for s in snapshots)
    expected_operator = {"hint_index_merge":"IndexMerge", "hint_hash_join":"HashJoin",
                         "hint_merge_join":"MergeJoin", "hint_inl_join":"IndexJoin",
                         "hint_inl_hash_join":"IndexHashJoin", "hint_hash_agg":"HashAgg",
                         "hint_stream_agg":"StreamAgg", "hint_use_index":"IndexRangeScan"}.get(case["name"])
    def operators(node):
        yield node.operator
        for child in node.children:
            yield from operators(child)
    if expected_operator:
        assert all(expected_operator in {op for node in ir.roots for op in operators(node)} for ir in plans)
    if q.parameters is not None:
        assert all(s.context["capture_mode"] == "prepared_explain" for s in snapshots)


def test_complete_matrix_bundle_online_offline(live, monkeypatch):
    profiles, root = live
    source = root / "queries.jsonl"
    write_jsonl(source, CASES)
    bundle = api.export(ExportRequest(str(root / "bundle"), source="source", sql_file=str(source), statement_types=ALLOWED), profiles=profiles)
    assert bundle.manifest["run_status"] == "completed", bundle.issues
    loaded = load_bundle(bundle.path)
    assert len(loaded.queries) == len(CASES)
    assert [(q.sql_text,q.parameters) for q in loaded.queries] == [(c["sql_text"],c["parameters"]) for c in CASES]
    result = api.compare(CompareRequest(bundle.path,"old","new",str(root / "comparison")), profiles=profiles)
    write_json(root / "comparison-evidence.json", {"summary":result.summary,"issues":result.issues})
    assert result.exit_code == 0, result.summary
    assert result.summary["full_comparisons"] == str(len(CASES))
    monkeypatch.setattr(api, "Database", lambda *a,**k: pytest.fail("Offline replay must not connect"))
    offline = api.diff(DiffRequest(result.output_dir,str(root / "offline")))
    assert offline.summary == result.summary
    assert verify(root / "comparison")["business_fingerprint"] == verify(root / "offline")["business_fingerprint"]


@pytest.mark.parametrize("sql,parameters,truncated,code", [
    ("SELECT * FROM orders WHERE id=?",None,False,"NEEDS_EXECUTABLE_SAMPLE"),
    ("EXECUTE old_stmt USING @p",None,False,"NEEDS_EXECUTABLE_SAMPLE"),
    ("SELECT * FROM orders WHERE id=?",[],False,"NEEDS_EXECUTABLE_SAMPLE"),
    ("SELECT * FROM orders WHERE id=7",None,True,"NEEDS_EXECUTABLE_SAMPLE"),
    ("SELECT /*+ SET_VAR(max_execution_time=0) */ id FROM orders",None,False,"SQL_REJECTED"),
    ("SELECT id FROM orders FOR UPDATE",None,False,"SQL_REJECTED"),
    ("SELECT id FROM orders; DELETE FROM orders",None,False,"SQL_REJECTED"),
    ("SELECT * FROM orders WHERE id=?",[{"type":"sql","value":"1 OR 1=1"}],False,"INVALID_PARAMETERS"),
])
def test_incomplete_or_unsafe_never_captured(live,sql,parameters,truncated,code):
    profiles, _ = live
    q = make_query(sql,SCHEMA,parameters=parameters,truncated=truncated)
    def no_connection(*args,**kwargs):
        pytest.fail("Rejected input must not connect")
    snapshot = capture_plan(q,"old",profiles["old"],db_factory=no_connection)
    assert snapshot.capture_status in {"skipped","needs_sample"}
    assert code in {i["code"] for i in snapshot.issues}


def test_actual_prepared_summary_requires_explicit_parameter_sample(live):
    profiles, root = live
    marker = "pcc_prepared_" + digest(str(root))[:12]
    sql = f"SELECT id AS {marker},note FROM orders WHERE id=? AND note<>?"
    with Database(profiles["old"]) as db:
        db.query(f"USE `{SCHEMA}`")
        db.query("PREPARE pcc_source FROM %s", (sql,))
        db.query("SET @pcc_a=7,@pcc_b=%s", ("?,;中文",))
        db.query("EXECUTE pcc_source USING @pcc_a,@pcc_b")
        db.query("DEALLOCATE PREPARE pcc_source")
        rows = db.records("SELECT DIGEST,QUERY_SAMPLE_TEXT FROM information_schema.STATEMENTS_SUMMARY WHERE SCHEMA_NAME=%s AND STMT_TYPE='Select' AND DIGEST_TEXT LIKE %s", (SCHEMA,"%"+marker+"%"))
    assert len(rows) == 1
    sample = rows[0]
    unresolved = make_query(sample["QUERY_SAMPLE_TEXT"],SCHEMA)
    assert unresolved.executable_status == "needs_sample"
    parameters = [{"type":"int","value":"7"},{"type":"string","value":"?,;中文"}]
    source = root / "prepared-supplement.jsonl"
    write_jsonl(source,[{"schema":SCHEMA,"sql_text":sql,"parameters":parameters,"source_digest":sample["DIGEST"]}])
    query = api._query_file(str(source),ALLOWED)[0]
    assert query.source_digest == sample["DIGEST"] and query.executable_status == "ok"
    snapshot = capture_plan(query,"old",profiles["old"],format="tidb_json")
    assert snapshot.capture_status == "ok" and parse_plan(snapshot).parse_status == "ok"
    write_json(root / "prepared-summary.json", {"original":sample,"unresolved":unresolved,"supplement":query,"snapshot":snapshot})


def test_actual_long_summary_truncation_is_not_executable(live):
    from pcc_tool.collector.source import read_summary
    profiles, root = live
    sql = "SELECT /*" + "中文?;" * 1024 + "*/ id AS pcc_long_matrix_marker FROM orders WHERE id=7"
    with Database(profiles["old"]) as db:
        db.query(f"USE `{SCHEMA}`")
        db.query(sql)
        selected = db.records("SELECT DIGEST FROM information_schema.STATEMENTS_SUMMARY WHERE SCHEMA_NAME=%s AND DIGEST_TEXT LIKE %s", (SCHEMA,"%pcc_long_matrix_marker%"))
        assert len(selected) == 1
        rows, _ = read_summary(db,"old")
    matching = [r for r in rows if r["schema_name"] == SCHEMA and r["sql_digest"] == selected[0]["DIGEST"]]
    assert matching
    for row in matching:
        assert row["truncated"] is True
        q = make_query(row["query_sample_text"],SCHEMA,truncated=row["truncated"])
        assert q.executable_status == "needs_sample"
    write_json(root / "long-summary-truncation.json", {"original_bytes":len(sql.encode()),"rows":matching})
