import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pcc_tool import api
from pcc_tool.artifacts.store import finalize, load_bundle, read_json, read_jsonl, verify, write_bytes, write_json, write_jsonl
from pcc_tool.models import (CaptureBundle, CompareRequest, DiffRequest, ExportRequest, PCCError, PlanSnapshot,
                            PrepareRequest, QueryCase, VERSIONS, business_value, digest)


def archive(path, queries, plans, issues=None):
    bundle = CaptureBundle({"kind": "CaptureBundle", "contract_version": "1", "objects": []}, queries)
    result, irs = api._summarize(queries, plans, "original", issues or [])
    api._write_result(path, result, bundle, plans, irs, api.Events())
    finalize(path, {"kind": "ComparisonResult", "versions": VERSIONS})
    return result


def test_cli_sdk_offline_same_and_no_database(tmp_path, monkeypatch, snapshot):
    q = QueryCase.create("SELECT 1", "app")
    plans = {(q.query_id, "old"): snapshot([["TableDual_1", "1", "root", "", "rows:1"]], query_id=q.query_id),
             (q.query_id, "new"): snapshot([["TableDual_4", "2", "root", "", "rows:2"]], side="new", query_id=q.query_id)}
    source = tmp_path / "source"
    original = archive(source, [q], plans)
    def forbid(*a, **k):
        raise AssertionError("offline diff connected to DB")
    monkeypatch.setattr(api, "Database", forbid)
    sdk = api.diff(DiffRequest(str(source), str(tmp_path / "sdk")))
    assert sdk.exit_code == 1
    assert sdk.summary == original.summary
    assert business_value(sdk.diffs) == business_value(original.diffs)
    process = subprocess.run([sys.executable, "-m", "pcc_tool", "--config", "/nonexistent", "--events", "jsonl",
                              "diff", "--result-dir", str(source), "--out", str(tmp_path / "cli")], capture_output=True, text=True)
    assert process.returncode == 1, process.stderr
    assert all(json.loads(line)["contract_version"] == "1" for line in process.stdout.splitlines())
    assert read_json(tmp_path / "sdk/summary.json") == read_json(tmp_path / "cli/summary.json")
    assert read_jsonl(tmp_path / "sdk/diffs.jsonl") == read_jsonl(tmp_path / "cli/diffs.jsonl")


def test_missing_samples_denominator_and_partial_precedence(tmp_path, snapshot):
    q = QueryCase.create("SELECT 1")
    missing = QueryCase.create(None, origin={"source": "one"}, executable_status="needs_sample")
    failed = QueryCase.create("SELECT 2")
    plans = {(q.query_id, s): snapshot([["TableDual_1", str(i), "root", "", ""]], side=s, query_id=q.query_id)
             for i, s in enumerate(("old", "new"), 1)}
    plans[(failed.query_id, "old")] = PlanSnapshot(failed.query_id, "old", capture_status="failed")
    plans[(failed.query_id, "new")] = PlanSnapshot(failed.query_id, "new", capture_status="failed")
    result = archive(tmp_path / "report", [q, missing, failed], plans)
    assert result.exit_code == 3
    assert result.summary["selected_samples"] == "3"
    assert result.summary["changed"] == "1" and result.summary["incomparable"] == "1" and result.summary["not_run"] == "1"
    assert result.summary["needs_sample"] == "1"
    assert sum(int(result.summary[k]) for k in ("changed", "same", "incomparable", "not_run")) == 3


def test_html_escaping_and_csv_formula(tmp_path, snapshot):
    q = QueryCase.create("<script>alert(1)</script>", "=HYPERLINK(1)")
    result = archive(tmp_path / "report", [q], {})
    html = (tmp_path / "report/report.html").read_text()
    csv = (tmp_path / "report/differences.csv").read_text("utf-8-sig")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "'=HYPERLINK" in csv


def test_export_sql_file_and_prepare_no_db(tmp_path, monkeypatch):
    path = tmp_path / "queries.jsonl"
    path.write_text(json.dumps({"sql_text": "SELECT 1", "schema": "", "context": {}}) + "\n")
    def forbid(*a, **k):
        raise AssertionError("unexpected connection")
    monkeypatch.setattr(api, "Database", forbid)
    bundle = api.export(ExportRequest(str(tmp_path / "bundle"), sql_file=str(path)))
    assert len(bundle.queries) == 1 and bundle.manifest["run_status"] == "completed"
    prep = api.prepare(PrepareRequest(bundle.path, ("old", "new"), str(tmp_path / "prep")))
    assert prep["execution"] == "manual_only"
    assert (tmp_path / "prep/old/import_commands.sql").exists()
    assert not "--apply" in subprocess.run([sys.executable, "-m", "pcc_tool", "prepare", "--help"], capture_output=True, text=True).stdout


def test_metadata_only_legacy_is_not_sql(tmp_path):
    (tmp_path / "top_sql_selection.sql").write_text("SELECT 1")
    with pytest.raises(PCCError) as err:
        api.export(ExportRequest(str(tmp_path / "out"), legacy_package=str(tmp_path)))
    assert err.value.code == "NEEDS_EXECUTABLE_SAMPLE"


def test_fingerprint_excludes_timing():
    a = {"sql": "SELECT '2026-10-01'", "captured_at": "first", "nested": {"time": "first", "value": 1}}
    b = {"sql": "SELECT '2026-10-01'", "captured_at": "second", "nested": {"time": "second", "value": 1}}
    assert digest(business_value(a)) == digest(business_value(b))
    b["sql"] = "SELECT '2026-10-02'"
    assert digest(business_value(a)) != digest(business_value(b))


def test_incomplete_plan_never_same(snapshot):
    q = QueryCase.create("SELECT 1")
    plans = {(q.query_id, s): snapshot([["FutureOperator_1", "1", "root", "", ""]], side=s, query_id=q.query_id) for s in ("old", "new")}
    result, _ = api._summarize([q], plans, "fp", [])
    assert result.summary["incomparable"] == "1" and result.exit_code == 3


def test_context_difference_never_silent(snapshot):
    q = QueryCase.create("SELECT 1")
    plans = {(q.query_id, s): snapshot([["TableDual_1", "1", "root", "", ""]], side=s, query_id=q.query_id,
                  context={"session": {"sql_mode": s}}) for s in ("old", "new")}
    result, _ = api._summarize([q], plans, "fp", [])
    assert result.exit_code == 3
    assert any(i["code"] == "CONTEXT_DIFFERENCE" for i in result.issues)


def test_version_specific_default_difference_is_visible_warning(snapshot):
    q = QueryCase.create("SELECT 1")
    plans = {(q.query_id, s): snapshot([["TableDual_1", "1", "root", "", ""]], side=s, query_id=q.query_id,
                  context={"session": {"sql_mode": "", "tidb_cost_model_version": str(i)}, "declared_session": {}})
             for i, s in enumerate(("old", "new"), 1)}
    result, _ = api._summarize([q], plans, "fp", [])
    assert result.exit_code == 0
    assert any(i["code"] == "CONTEXT_DIFFERENCE" and i["severity"] == "warning" for i in result.issues)
    for plan in plans.values():
        plan.context["declared_session"] = {"tidb_cost_model_version": plan.context["session"]["tidb_cost_model_version"]}
    assert api._summarize([q], plans, "fp", [])[0].exit_code == 3
