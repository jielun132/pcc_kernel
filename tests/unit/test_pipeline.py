import json
import threading

import pytest

from pcc_tool import api
from pcc_tool.artifacts.store import verify
from pcc_tool.models import CompareRequest, DiffRequest, ExportRequest, PCCError, PlanSnapshot


@pytest.fixture
def online(tmp_path, monkeypatch):
    query_file = tmp_path / "queries.jsonl"
    query_file.write_text("".join(json.dumps({"sql_text": f"SELECT {i}"}) + "\n" for i in range(4)))
    bundle = api.export(ExportRequest(str(tmp_path / "bundle"), sql_file=str(query_file)))
    profiles = {s: {"host": s, "user": "reader", "role": "test", "expected_version": v}
                for s, v in [("old", "v6.5.12"), ("new", "v8.5.4")]}

    class DB:
        def __init__(self, profile, *args):
            self.profile = profile
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def version(self):
            return self.profile["expected_version"]
        def variables(self):
            return {"sql_mode": ""}
    monkeypatch.setattr(api, "Database", DB)
    monkeypatch.setattr(api, "negotiate_format", lambda db: ["row"])
    monkeypatch.setattr(api, "verify_materials", lambda db, m: ({}, []))
    calls = []
    def capture(q, side, profile, **kwargs):
        calls.append((q.query_id, side))
        return PlanSnapshot(q.query_id, side, version=profile["expected_version"],
                            columns=["id", "estRows", "task", "access object", "operator info"],
                            rows=[["TableDual_1", "1", "root", "", "rows:1"]])
    monkeypatch.setattr(api, "capture_plan", capture)
    return bundle, profiles, calls, capture


def test_online_to_offline_equal(tmp_path, online):
    bundle, profiles, calls, _ = online
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "result")), profiles=profiles)
    assert result.exit_code == 0 and result.summary["same"] == "4" and len(calls) == 8
    assert verify(tmp_path / "result")["kind"] == "ComparisonResult"
    offline = api.diff(DiffRequest(str(tmp_path / "result"), str(tmp_path / "offline")))
    assert offline.exit_code == result.exit_code
    assert offline.summary == result.summary and offline.diffs == result.diffs


def test_partial_failures_and_repeated_material_checks_replay_identically(tmp_path, monkeypatch, online):
    bundle, profiles, _, capture = online
    monkeypatch.setattr(api, "verify_materials", lambda db, manifest: ({}, [{"code":"STATS_NOT_READY"}]))
    def failed(q, side, profile, **kwargs):
        snapshot = capture(q, side, profile, **kwargs)
        if side == "old":
            snapshot.capture_status = "failed"
            snapshot.issues = [{"code":"DB_QUERY_FAILED","message":"1105"}]
        return snapshot
    monkeypatch.setattr(api,"capture_plan",failed)
    result = api.compare(CompareRequest(bundle.path,"old","new",str(tmp_path / "partial")),profiles=profiles)
    assert result.exit_code == 3 and result.summary["incomparable"] == "4"
    first = api.diff(DiffRequest(result.output_dir,str(tmp_path / "offline1")))
    second = api.diff(DiffRequest(first.output_dir,str(tmp_path / "offline2")))
    assert result.items == first.items == second.items
    assert result.summary == first.summary == second.summary
    assert {verify(tmp_path / folder)["business_fingerprint"] for folder in ("partial","offline1","offline2")} == {verify(tmp_path / "partial")["business_fingerprint"]}
    for item in second.items:
        assert len(item["issues"]) == 3
        assert {i["code"] for i in item["issues"]} == {"STATS_NOT_READY","DB_QUERY_FAILED"}


def test_interrupt_and_resume(tmp_path, monkeypatch, online):
    bundle, profiles, calls, capture = online
    cancel = threading.Event()
    def interrupting(q, side, profile, **kwargs):
        result = capture(q, side, profile, **kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(api, "capture_plan", interrupting)
    output = str(tmp_path / "result")
    interrupted = api.compare(CompareRequest(bundle.path, "old", "new", output, concurrency=1), profiles=profiles, cancel=cancel)
    assert interrupted.exit_code == 4 and not (tmp_path / "result").exists()
    assert verify(tmp_path / ".result.partial")["run_status"] == "interrupted"
    monkeypatch.setattr(api, "capture_plan", capture)
    resumed = api.compare(CompareRequest(bundle.path, "old", "new", output, resume=True, concurrency=1), profiles=profiles)
    assert resumed.exit_code == 0
    assert len(calls) == len(set(calls)) == 8


def test_resume_rejects_version_drift(tmp_path, online):
    bundle, profiles, calls, _ = online
    cancel = threading.Event()
    cancel.set()
    output = str(tmp_path / "result")
    api.compare(CompareRequest(bundle.path, "old", "new", output), profiles=profiles, cancel=cancel)
    profiles["new"]["expected_version"] = "v8.5.5"
    with pytest.raises(PCCError) as err:
        api.compare(CompareRequest(bundle.path, "old", "new", output, resume=True), profiles=profiles)
    assert err.value.code == "RESUME_MISMATCH"


def test_drift_never_completed(tmp_path, monkeypatch, online):
    bundle, profiles, _, _ = online
    count = {}
    def verify_materials(db, manifest):
        key = db.profile["host"]
        count[key] = count.get(key, 0) + 1
        return {"ddl_hash": str(count[key])}, []
    monkeypatch.setattr(api, "verify_materials", verify_materials)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "result")), profiles=profiles)
    assert result.exit_code == 3
    assert all(i["compare_status"] == "incomparable" for i in result.items)
    assert any(i["code"] == "ENVIRONMENT_DRIFT" for i in result.issues)


def test_target_must_be_isolated(tmp_path, online):
    bundle, profiles, _, _ = online
    profiles["old"]["role"] = "source"
    with pytest.raises(PCCError) as err:
        api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "result")), profiles=profiles)
    assert err.value.code == "TARGET_NOT_ISOLATED"
