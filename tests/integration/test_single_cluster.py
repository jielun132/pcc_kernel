"""Opt-in real TiDB checks. Two nodes share a cluster; this is not a version comparison.

Run live_setup.py separately with explicit approval before enabling this suite.
Credentials are read exclusively through the configured environment references.
"""
import dataclasses
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pcc_tool import api
from pcc_tool.artifacts.store import read_jsonl, verify, write_json, write_jsonl
from pcc_tool.capture.connection import Database, load_profiles, status_json, verify_status_identity
from pcc_tool.capture.explain import capture_plan
from pcc_tool.collector.dependencies import verify_materials
from pcc_tool.collector.summary import make_query
from pcc_tool.models import (CollectRequest, CompareRequest, DiffRequest, DoctorRequest,
                             ExportRequest, PCCError, PrepareRequest, business_value, digest)
from pcc_tool.models import QueryCase
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan

SCHEMA = "pcc_validation_20261011"
CASES = {
    "point_get": "SELECT * FROM orders WHERE id=7",
    "batch_point_get": "SELECT * FROM orders WHERE id IN (7,17,27)",
    "index_range": "SELECT * FROM orders WHERE customer_id BETWEEN 2 AND 4",
    "table_scan": "SELECT * FROM orders IGNORE INDEX(idx_amount,idx_status_amount,idx_customer) WHERE amount>100",
    "covering_index": "SELECT amount FROM orders FORCE INDEX(idx_amount) WHERE amount BETWEEN 101 AND 202",
    "index_lookup": "SELECT note FROM orders FORCE INDEX(idx_amount) WHERE amount BETWEEN 101 AND 202",
    "index_merge": "SELECT /*+ USE_INDEX_MERGE(o,idx_customer,idx_amount) */ * FROM orders o WHERE customer_id=3 OR amount=17",
    "hash_join": "SELECT /*+ HASH_JOIN(o,c) */ o.id,c.name FROM orders o JOIN customers c ON o.customer_id=c.id WHERE o.amount>100",
    "merge_join": "SELECT /*+ MERGE_JOIN(o,c) */ o.id,c.name FROM orders o JOIN customers c ON o.customer_id=c.id",
    "index_join": "SELECT /*+ INL_JOIN(o) */ c.name,o.id FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2",
    "semi_join": "SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id=o.customer_id AND c.region=1)",
    "correlated": "SELECT o.id,(SELECT MAX(i.price) FROM items i WHERE i.order_id=o.id) FROM orders o WHERE o.id<20",
    "self_join": "SELECT a.id,b.id FROM orders a JOIN orders b ON a.customer_id=b.customer_id WHERE a.id<4 AND b.id<8",
    "cte": "WITH high_orders AS (SELECT id,amount FROM orders WHERE amount>1000) SELECT COUNT(*) FROM high_orders",
    "cte_reuse": "WITH totals AS (SELECT customer_id,SUM(amount) AS total FROM orders GROUP BY customer_id) SELECT a.customer_id FROM totals a JOIN totals b ON a.customer_id=b.customer_id WHERE a.total>10000",
    "recursive_cte": "WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<5) SELECT * FROM seq",
    "union_all": "SELECT id FROM orders WHERE id<3 UNION ALL SELECT id FROM orders WHERE id>998",
    "union_distinct": "SELECT customer_id FROM orders WHERE id<8 UNION SELECT id FROM customers WHERE id<8",
    "nested_view": "SELECT * FROM nested_view WHERE customer_id=2",
    "partition": "SELECT * FROM partitioned_orders WHERE id BETWEEN 90 AND 110 AND amount>100",
    "aggregation": "SELECT status,COUNT(*),SUM(amount) FROM orders GROUP BY status HAVING COUNT(*)>10",
    "topn": "SELECT id,note FROM orders ORDER BY amount DESC LIMIT 10 OFFSET 2",
    "sort": "SELECT id,note FROM orders ORDER BY note,id",
    "window": "SELECT id,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC) AS rn FROM orders WHERE id<20",
    "update": "UPDATE orders SET amount=amount+1 WHERE customer_id=3 AND id<20",
    "delete": "DELETE FROM orders WHERE customer_id=3 AND id<20",
    "literal_percent": "SELECT id FROM orders WHERE note LIKE '%100%' OR note='%s'",
}

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("PCC_RUN_SINGLE_CLUSTER") != "1", reason="Single-cluster live test is explicitly opt-in")]


@pytest.fixture(scope="module")
def live():
    profiles = load_profiles(os.environ["PCC_TEST_CONFIG"])
    assert all(profiles[k].get("expected_version") for k in ("source", "old", "new"))
    root = Path(os.environ["PCC_LIVE_OUTPUT"])
    root.mkdir(parents=True, exist_ok=True)
    with Database(profiles["source"]) as db:
        # Other opted-in suites add their own fixtures to this same approved schema.
        assert db.scalar("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ('customers','orders','items','partitioned_orders','pseudo_fixture','drift_fixture','order_view','nested_view')", (SCHEMA,)) == "8"
    return profiles, root


def write_cases(path, cases):
    write_jsonl(path, [{"schema": SCHEMA, "sql_text": sql, "case": name} for name, sql in cases.items()])


@pytest.fixture(scope="module")
def bundle(live):
    profiles, root = live
    path = root / "queries.jsonl"
    write_cases(path, CASES)
    # Compare each persisted file with the bytes from that exact HTTP response;
    # successive dumps can change predicate-column usage timestamps and order.
    from pcc_tool.collector import dependencies
    original = dependencies.status_json
    observed = {}
    def observe(profile, parts, **kwargs):
        result = original(profile, parts, **kwargs)
        if kwargs.get("raw"):
            observed[parts[-1]] = result
        return result
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(dependencies, "status_json", observe)
        bundle = api.export(ExportRequest(str(root / "bundle"), source="source", sql_file=str(path)), profiles=profiles)
    for obj in bundle.manifest["objects"]:
        if obj.get("stats_file"):
            assert (Path(bundle.path) / obj["stats_file"]).read_bytes() == observed[obj["table"]]
    write_json(root / "stats-byte-preservation.json", {"files_verified": len(observed), "unchanged": True})
    write_json(root / "export-evidence.json", {"run_status": bundle.manifest["run_status"], "issues": bundle.issues})
    assert bundle.manifest["run_status"] == "completed", bundle.issues
    assert len(bundle.queries) == len(CASES)
    return bundle


def test_doctor_and_status_identity(live):
    profiles, root = live
    result = api.doctor(DoctorRequest(("source", "old", "new")), profiles=profiles)
    write_json(root / "doctor.json", result)
    assert result["run_status"] == "completed", result["issues"]
    assert all("row" in r["formats"] and "tidb_json" in r["formats"] for r in result["targets"].values())
    with Database(profiles["source"]) as db:
        identity = verify_status_identity(db, profiles["source"])
        write_json(root / "status-identity.json", identity)
        assert identity["verified"] is True


@pytest.mark.parametrize("fmt", ["row", "tidb_json"])
@pytest.mark.parametrize("name", CASES)
def test_real_plan_shapes(live, fmt, name):
    profiles, root = live
    q = make_query(CASES[name], SCHEMA)
    snapshot = capture_plan(q, "old", profiles["old"], format=fmt, stats_retries=1)
    ir = parse_plan(snapshot)
    write_json(root / "goldens" / f"{name}-{fmt}.json", {"query": q, "snapshot": snapshot, "ir": ir})
    assert snapshot.capture_status == "ok", snapshot.issues
    assert not snapshot.issues, snapshot.issues
    assert ir.parse_status == "ok", ir.issues


def test_export_views_stats_and_prepare(live, bundle, monkeypatch):
    profiles, root = live
    objects = bundle.manifest["objects"]
    names = [o["table"] for o in objects]
    assert set(names) == {"orders", "customers", "items", "partitioned_orders", "order_view", "nested_view"}
    assert names.index("orders") < names.index("order_view") < names.index("nested_view")
    for obj in objects:
        if obj["kind"] == "table":
            assert obj["stats_ready"] and obj["stats_evidence"]["histograms"]
            assert obj["stats_fingerprint"]
    with Database(profiles["new"]) as db:
        _, issues = verify_materials(db, bundle.manifest)
        assert not issues, issues
    def no_database(*args, **kwargs):
        pytest.fail("prepare must not open a database connection")
    monkeypatch.setattr(api, "Database", no_database)
    result = api.prepare(PrepareRequest(bundle.path, ("old", "new"), str(root / "prepared")), profiles=profiles)
    assert result["execution"] == "manual_only" and result["run_status"] == "completed"
    manifest = verify(root / "prepared")
    assert manifest["kind"] == "Preparation"
    script = (root / "prepared/old/import_commands.sql").read_text()
    assert "LOAD STATS '" in script
    assert not any(line.strip().upper().startswith(("DROP ", "TRUNCATE ", "ANALYZE ")) for line in script.splitlines())
    assert not (root / "prepared/old").is_symlink()


@pytest.fixture(scope="module")
def comparison(live, bundle):
    profiles, root = live
    with Database(profiles["source"]) as db:
        before = db.query(f"SELECT COUNT(*),SUM(id),SUM(amount) FROM `{SCHEMA}`.orders")
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(root / "comparison")), profiles=profiles)
    write_json(root / "comparison-evidence.json", {"status": result.run_status, "summary": result.summary, "issues": result.issues})
    with Database(profiles["source"]) as db:
        after = db.query(f"SELECT COUNT(*),SUM(id),SUM(amount) FROM `{SCHEMA}`.orders")
    assert before == after, "EXPLAIN must not execute UPDATE or DELETE"
    write_json(root / "data-unchanged.json", {"before": before, "after": after})
    return result


def test_two_node_compare_and_offline_parity(live, comparison, monkeypatch):
    _, root = live
    assert comparison.exit_code == 0, comparison.issues
    assert comparison.summary["same"] == str(len(CASES))
    assert comparison.summary["full_comparisons"] == str(len(CASES))
    def no_database(*args, **kwargs):
        pytest.fail("offline diff must not open a database connection")
    monkeypatch.setattr(api, "Database", no_database)
    offline = api.diff(DiffRequest(comparison.output_dir, str(root / "offline")))
    assert offline.summary == comparison.summary and offline.diffs == comparison.diffs
    for file in ("report.html", "summary.json", "items.jsonl", "diffs.jsonl", "issues.jsonl"):
        assert (root / "offline" / file).is_file()
    assert verify(root / "offline")["business_fingerprint"] == verify(root / "comparison")["business_fingerprint"]


def test_cli_offline_ignores_credentials(live, comparison):
    _, root = live
    environment = {k: v for k, v in os.environ.items() if k != "TIDB_DB_PASSWORD"}
    command = [sys.executable, "-m", "pcc_tool", "--config", "/unavailable/credentials.json", "--events", "jsonl",
               "diff", "--result-dir", comparison.output_dir, "--out", str(root / "cli-offline")]
    process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    (root / "cli-offline.stdout.jsonl").write_text(process.stdout)
    assert process.returncode == comparison.exit_code, process.stderr
    assert all(json.loads(line)["contract_version"] == "1" for line in process.stdout.splitlines())


def test_interrupt_resume_and_fingerprint(live, bundle):
    profiles, root = live
    cancel = threading.Event()
    events = []
    def interrupt(event):
        events.append(event)
        if event["event"] == "plan_captured":
            cancel.set()
    request = CompareRequest(bundle.path, "old", "new", str(root / "resume"), concurrency=1)
    first = api.compare(request, profiles=profiles, on_event=interrupt, cancel=cancel)
    assert first.exit_code == 4
    assert not (root / "resume").exists()
    assert verify(root / ".resume.partial")["run_status"] == "interrupted"
    successful = {(r["query_id"], r["side"]) for r in events if r["event"] == "plan_captured" and r["capture_status"] == "ok"}
    with pytest.raises(PCCError) as error:
        api.compare(dataclasses.replace(request, resume=True, session={"tidb_enable_index_merge": 0}), profiles=profiles)
    assert error.value.code == "RESUME_MISMATCH"
    resumed_events = []
    second = api.compare(dataclasses.replace(request, resume=True), profiles=profiles, on_event=resumed_events.append)
    assert second.exit_code == 0, second.issues
    captured_again = {(r["query_id"], r["side"]) for r in resumed_events if r["event"] == "plan_captured"}
    assert not successful & captured_again
    assert len(successful | captured_again) == 2 * len(CASES)
    write_json(root / "resume-evidence.json", {"first_exit": first.exit_code, "resumed_exit": second.exit_code,
                                              "reused": len(successful), "new_captures": len(captured_again),
                                              "fingerprint_change_rejected": True})


def test_pseudo_stats_missing_sample_and_rejection(live):
    profiles, root = live
    cases = {"pseudo": "SELECT * FROM pseudo_fixture WHERE value=1", "missing": "SELECT * FROM orders WHERE id=?",
             "unsafe": "SELECT SLEEP(5)", "multi": "SELECT 1; DELETE FROM orders"}
    path = root / "negative-queries.jsonl"
    write_cases(path, cases)
    bundle = api.export(ExportRequest(str(root / "negative-bundle"), source="source", sql_file=str(path)), profiles=profiles)
    assert len(bundle.queries) == 4 and any(q.executable_status == "needs_sample" for q in bundle.queries)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(root / "negative-result"), stats_retries=0), profiles=profiles)
    assert result.exit_code == 3
    assert result.summary["selected_samples"] == "4" and result.summary["needs_sample"] == "1"
    assert result.summary["full_comparisons"] == "0"
    assert any(i["code"] == "STATS_NOT_READY" for i in result.issues)
    write_json(root / "negative-evidence.json", {"summary": result.summary, "issues": result.issues})


def test_warning_capture_and_real_change(live):
    profiles, root = live
    warning = capture_plan(make_query("SELECT /*+ USE_INDEX(orders,absent_index) */ * FROM orders WHERE amount=100", SCHEMA),
                           "old", profiles["old"], format="row")
    write_json(root / "warning-snapshot.json", warning)
    assert warning.capture_status == "ok" and warning.warnings
    q = make_query("SELECT * FROM orders WHERE customer_id=3 OR amount=17", SCHEMA)
    off = capture_plan(q, "old", profiles["old"], format="row", session={"tidb_enable_index_merge": 0})
    on = capture_plan(q, "new", profiles["new"], format="row", session={"tidb_enable_index_merge": 1})
    diffs, review = compare_ir(parse_plan(off), parse_plan(on))
    write_json(root / "controlled-change.json", {"query": q, "off": off, "on": on, "diffs": diffs, "review": review})
    assert diffs and not review
    result, _ = api._summarize([q], {(q.query_id, "old"): off, (q.query_id, "new"): on}, "controlled", [])
    assert result.summary["incomparable"] == "1"
    assert any(i["code"] == "CONTEXT_DIFFERENCE" for i in result.issues)


def test_same_endpoint_and_wrong_version_rejected(live, bundle):
    profiles, root = live
    profiles = {**profiles, "same": profiles["old"], "wrong_version": {**profiles["old"], "expected_version": "v0.0.0"}}
    with pytest.raises(PCCError) as error:
        api.compare(CompareRequest(bundle.path, "old", "same", str(root / "must-not-publish")), profiles=profiles)
    assert error.value.code == "TARGET_NOT_ISOLATED"
    result = api.doctor(DoctorRequest(("wrong_version",)), profiles=profiles)
    assert result["run_status"] == "partial" and result["issues"][0]["code"] == "VERSION_MISMATCH"


def test_collect_summary_spool_and_repeated_snapshot(live):
    profiles, root = live
    marker = "pcc_live_sum_" + digest(str(root))[:12]
    sql = f"SELECT SUM(amount) AS {marker} FROM orders WHERE status=2"
    for name, count in (("source", 3), ("old", 5), ("new", 7)):
        with Database(profiles[name]) as db:
            db.query(f"USE `{SCHEMA}`")
            for _ in range(count):
                db.query(sql)
    spool = str(root / "spool")
    one = api.collect(CollectRequest("source", spool), profiles=profiles)
    two = api.collect(CollectRequest("source", spool), profiles=profiles)
    assert one["batches"] == two["batches"] == "1"
    begin = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    request = ExportRequest(str(root / "spool-bundle"), source="source", spool=spool, begin=begin, end=end,
                            schemas=(SCHEMA,), top_n=100)
    bundle = api.export(request, profiles=profiles)
    assert bundle.queries and bundle.selection
    matches = [q for q in bundle.queries if q.sql_text and marker in q.sql_text]
    assert len(matches) == 1, [(q.sql_text, q.metrics) for q in matches]
    assert int(matches[0].metrics["execution_count"]) == 15, matches[0].metrics
    direct = api.export(dataclasses.replace(request, out=str(root / "summary-bundle"), spool=None, from_summary=True), profiles=profiles)
    direct_match = [q for q in direct.queries if q.sql_text and marker in q.sql_text]
    assert len(direct_match) == 1 and int(direct_match[0].metrics["execution_count"]) == 15
    write_json(root / "summary-evidence.json", {"spool_metrics": matches[0].metrics, "direct_metrics": direct_match[0].metrics,
                                               "selection": bundle.selection, "issues": bundle.issues})


def test_artifact_hash_tamper_rejected(live, comparison, tmp_path):
    import shutil
    copied = tmp_path / "tampered"
    shutil.copytree(comparison.output_dir, copied)
    with (copied / "plans/old.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(PCCError) as error:
        api.diff(DiffRequest(str(copied), str(tmp_path / "should-not-exist")))
    assert error.value.code == "HASH_MISMATCH"


def test_collect_watch_stops_after_published_batch(live):
    profiles, root = live
    cancel = threading.Event()
    events = []
    def stop(event):
        events.append(event)
        if event["event"] == "batch_published":
            cancel.set()
    result = api.collect(CollectRequest("source", str(root / "watch-spool"), watch=True, interval_seconds=1),
                         profiles=profiles, cancel=cancel, on_event=stop)
    assert result == {"run_status": "interrupted", "batches": "1"}
    from pcc_tool.collector.spool import read_spool
    rows, metadata = read_spool(str(root / "watch-spool"))
    assert rows and metadata
    write_json(root / "watch-evidence.json", {"result": result, "events": events})


def test_cancel_real_explain_confirms_server_termination(live):
    profiles, root = live
    running, cancel = threading.Event(), threading.Event()
    calls = []
    class ObservedDB(Database):
        def query(self, sql, params=()):
            if sql.startswith("EXPLAIN FORMAT="):
                running.set()
            elif sql.startswith("KILL TIDB QUERY"):
                calls.append(sql)
            return super().query(sql, params)
    sql = " UNION ALL ".join(f"SELECT id FROM orders WHERE amount={i} AND status={i%4}" for i in range(2000))
    query = QueryCase.create(sql, SCHEMA)
    snapshots = []
    worker = threading.Thread(target=lambda: snapshots.append(capture_plan(query, "old", profiles["old"],
        format="row", timeout_seconds=5, stats_retries=0, cancel=cancel, db_factory=ObservedDB)))
    worker.start()
    assert running.wait(20)
    cancel.set()
    worker.join(20)
    assert not worker.is_alive() and snapshots
    snapshot = snapshots[0]
    write_json(root / "cancel-evidence.json", {"sql_bytes": len(sql), "union_branches": 2000,
        "capture_status": snapshot.capture_status, "cancellation": snapshot.cancellation,
        "issues": snapshot.issues, "connection_id": snapshot.context.get("connection_id"), "kill_commands": calls})
    assert snapshot.capture_status == "failed" and snapshot.cancellation == "confirmed"
    assert any(i["code"] == "CANCELLED" for i in snapshot.issues)
    assert calls == ["KILL TIDB QUERY " + snapshot.context["connection_id"]]
    with Database(profiles["old"]) as db:
        active = db.records("SELECT ID, COMMAND FROM information_schema.CLUSTER_PROCESSLIST WHERE ID=%s", (snapshot.context["connection_id"],))
    assert not active or all(row["COMMAND"].lower() == "sleep" for row in active)


def test_fixture_stats_load_and_material_drift(live, bundle):
    profiles, root = live
    # This explicit test harness performs the approved import. PCC prepare stays read-only.
    source = profiles["source"]
    environment = {**os.environ, "MYSQL_PWD": os.environ[source["password_env"]]}
    statements = []
    for obj in bundle.manifest["objects"]:
        assert obj["schema"] == SCHEMA
        if obj.get("stats_file") and obj["table"] != "partitioned_orders":
            path = (Path(bundle.path) / obj["stats_file"]).resolve()
            payload = json.loads(path.read_text())
            assert payload["database_name"] == SCHEMA and payload["table_name"] == obj["table"]
            statements.append("LOAD STATS '" + str(path).replace("'", "''") + "';")
    process = subprocess.run(["mysql", "--local-infile=1", "--batch", "--host=" + source["host"],
                              "--port=" + str(source["port"]), "--user=" + source["user"]],
                             env=environment, input="\n".join(statements), text=True, capture_output=True, timeout=60)
    assert process.returncode == 0, "LOAD STATS failed (diagnostics retained without credentials)"
    with Database(source) as db:
        _, issues = verify_materials(db, bundle.manifest)
        assert not issues, issues
    write_json(root / "load-stats-evidence.json", {"imported_files": len(statements), "exit_code": process.returncode,
                                                 "post_import_issues": issues})
    path = root / "drift-query.jsonl"
    write_cases(path, {"drift": "SELECT * FROM drift_fixture WHERE value=1"})
    baseline = api.export(ExportRequest(str(root / "drift-bundle"), source="source", sql_file=str(path)), profiles=profiles)
    assert baseline.manifest["run_status"] == "completed", baseline.issues
    index = "idx_" + digest(str(root))[:10]
    with Database(source) as db:
        db.query(f"CREATE INDEX `{index}` ON `{SCHEMA}`.drift_fixture(value)")
        _, ddl_issues = verify_materials(db, baseline.manifest)
        assert any(i["code"] == "SCHEMA_MISMATCH" for i in ddl_issues)
        next_id = int(db.scalar(f"SELECT MAX(id)+1 FROM `{SCHEMA}`.drift_fixture"))
        db.query(f"INSERT INTO `{SCHEMA}`.drift_fixture VALUES (%s,%s)", (next_id, 9876))
        db.query(f"ANALYZE TABLE `{SCHEMA}`.drift_fixture")
        _, stats_issues = verify_materials(db, baseline.manifest)
        assert any(i["code"] == "STATS_NOT_READY" for i in stats_issues)
    result = api.compare(CompareRequest(baseline.path, "old", "new", str(root / "drift-result"), stats_retries=0), profiles=profiles)
    assert result.exit_code == 3 and result.summary["full_comparisons"] == "0"
    write_json(root / "drift-evidence.json", {"schema_issues": ddl_issues, "stats_issues": stats_issues,
                                            "compare_exit": result.exit_code, "inserted_rows": 1, "index": index})


def test_real_explain_deadline_confirms_server_termination(live):
    import time
    profiles, root = live
    sql = " UNION ALL ".join(
        f"SELECT o.id FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.amount={i} AND c.region={i%5}"
        for i in range(5000))
    query = QueryCase.create(sql, SCHEMA)
    start = time.monotonic()
    snapshot = capture_plan(query, "old", profiles["old"], format="row", timeout_seconds=1, stats_retries=0)
    write_json(root / "timeout-evidence.json", {"sql_bytes": len(sql), "union_branches": 5000,
        "configured_explain_deadline_seconds": 1, "total_including_client_parse_ms": int((time.monotonic()-start)*1000),
        "capture_status": snapshot.capture_status, "cancellation": snapshot.cancellation,
        "issues": snapshot.issues, "connection_id": snapshot.context.get("connection_id")})
    assert snapshot.capture_status == "failed" and snapshot.cancellation == "confirmed"
    assert any(i["code"] == "EXPLAIN_TIMEOUT" for i in snapshot.issues)
    with Database(profiles["old"]) as db:
        active = db.records("SELECT ID, COMMAND FROM information_schema.CLUSTER_PROCESSLIST WHERE ID=%s", (snapshot.context["connection_id"],))
    assert not active or all(row["COMMAND"].lower() == "sleep" for row in active)


def test_native_partition_stats_roundtrip_is_complete(live):
    """Keep a strict acceptance check for native import completeness; do not waive lost sketches."""
    profiles, root = live
    source = profiles["source"]
    with Database(source) as db:
        db.query(f"ANALYZE TABLE `{SCHEMA}`.partitioned_orders")
    path = root / "partition-replay-query.jsonl"
    write_cases(path, {"partition_replay": "SELECT * FROM partitioned_orders WHERE amount>100"})
    bundle = api.export(ExportRequest(str(root / "partition-replay-bundle"), source="source", sql_file=str(path)), profiles=profiles)
    assert bundle.manifest["run_status"] == "completed", bundle.issues
    obj = bundle.manifest["objects"][0]
    stats_path = (Path(bundle.path) / obj["stats_file"]).resolve()
    before = json.loads(stats_path.read_text())
    source_sketches = sum(bool(col.get("fm_sketch")) for part in before["partitions"].values()
                          for col in (part.get("columns") or {}).values())
    assert source_sketches > 0, "fixture must exercise partition FM Sketch preservation"
    process = subprocess.run(["mysql", "--local-infile=1", "--batch", "--host=" + source["host"],
                              "--port=" + str(source["port"]), "--user=" + source["user"]],
                             env={**os.environ, "MYSQL_PWD": os.environ[source["password_env"]]},
                             input="LOAD STATS '" + str(stats_path).replace("'", "''") + "';",
                             text=True, capture_output=True, timeout=60)
    assert process.returncode == 0
    after = status_json(source, ("stats", "dump", SCHEMA, "partitioned_orders"))
    target_sketches = sum(bool(col.get("fm_sketch")) for part in after["partitions"].values()
                          for col in (part.get("columns") or {}).values())
    with Database(source) as db:
        _, issues = verify_materials(db, bundle.manifest)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(root / "partition-replay-result")), profiles=profiles)
    if issues:
        assert result.exit_code == 3 and result.summary["full_comparisons"] == "0"
    write_json(root / "partition-replay-evidence.json", {"mysql_exit": process.returncode,
        "source_fm_sketches": source_sketches, "restored_fm_sketches": target_sketches,
        "issues": issues, "version": source["expected_version"], "compare_exit": result.exit_code,
        "full_comparisons": result.summary["full_comparisons"]})
    # Native TiDB v7.1.9-0.3 currently fails here: LOAD STATS drops these sketches.
    assert not issues and source_sketches == target_sketches, "Native partition statistics replay is incomplete"


def test_no_credentials_in_artifacts(live):
    profiles, root = live
    secret = os.environ[profiles["source"]["password_env"]].encode()
    if not secret:
        # Empty-password profiles carry no secret bytes to search for.
        assert "password" not in profiles["source"]
        return
    for path in root.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), f"Credential disclosure in {path.name}"
