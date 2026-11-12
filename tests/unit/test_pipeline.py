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


def test_warmup_only_archives_first_observation_and_formal_recaptures(tmp_path, online, monkeypatch):
    bundle, profiles, calls, capture = online
    attempts = {}
    def cold_capture(q, side, profile, **kwargs):
        key = q.query_id, side
        attempts[key] = attempts.get(key, 0) + 1
        value = capture(q, side, profile, **kwargs)
        if attempts[key] == 1:
            value.capture_status = "failed"
            value.issues = [{"code": "STATS_NOT_READY"}]
        return value
    monkeypatch.setattr(api, "capture_plan", cold_capture)
    warmup = tmp_path / "warmup"
    with monkeypatch.context() as guard:
        guard.setattr(api, "_summarize", lambda *a, **k: pytest.fail("Warmup compared plans"))
        guard.setattr(api, "render_reports", lambda *a, **k: pytest.fail("Warmup generated a report"))
        result = api.compare(CompareRequest(bundle.path, "old", "new", str(warmup), warmup_only=True, stats_retries=5), profiles=profiles)
    assert verify(warmup, "PlanWarmup")["run_status"] == "completed"
    assert result.summary["full_comparisons"] == "0" and not result.items and not result.diffs
    assert not (warmup / "report.html").exists() and set(attempts.values()) == {1}
    formal = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "formal")), profiles=profiles)
    assert formal.summary["same"] == "4" and formal.exit_code == 0 and set(attempts.values()) == {2}
    with pytest.raises(PCCError):
        api.diff(DiffRequest(str(warmup), str(tmp_path / "not-a-report")))


def test_warmup_resumes_without_reusing_first_observation_as_formal(tmp_path, online):
    from dataclasses import replace
    bundle, profiles, calls, _ = online
    stop = threading.Event()
    request = CompareRequest(bundle.path, "old", "new", str(tmp_path / "warmup"), warmup_only=True, concurrency=1)
    def event(value):
        if value["event"] == "plan_captured":
            stop.set()
    first = api.compare(request, profiles=profiles, on_event=event, cancel=stop)
    assert first.run_status == "interrupted" and not (tmp_path / "warmup").exists()
    second = api.compare(replace(request, resume=True), profiles=profiles)
    assert second.run_status == "completed" and len(calls) == len(set(calls)) == 8
    assert not (tmp_path / "warmup/report.html").exists()


def test_external_rule_policy_is_archived_and_inherited_offline(tmp_path, online):
    bundle, profiles, _, _ = online
    path=tmp_path/'policy.json'
    raw={'schema_version':1,'selected':['E01'],'thresholds':{'min_rows':2345},
         'cards':{'E01':{'review_note':'核对列来源'}},
         'profiles':[{'id':'custom','label':'Custom 6.5.12 → 8.5.4','old_version':'6.5.12','new_version':'8.5.4'}]}
    path.write_text(json.dumps(raw))
    result=api.compare(CompareRequest(bundle.path,'old','new',str(tmp_path/'configured'),rules_config=str(path)),profiles=profiles)
    assert result.assessment_policy['profile_id']=='custom'
    assert result.assessment_policy['thresholds']['min_rows']=='2345'
    path.rename(tmp_path/'moved-policy.json')
    offline=api.diff(DiffRequest(result.output_dir,str(tmp_path/'offline-configured')))
    assert offline.assessment_policy==result.assessment_policy
    assert offline.items==result.items and offline.summary==result.summary
    assert verify(tmp_path/'configured')['files']['assessment_rules.json']


def test_bad_rule_configuration_is_rejected_before_connection(tmp_path, online, monkeypatch):
    bundle, profiles, _, _=online
    path=tmp_path/'bad-policy.json';path.write_text('{"selected":["UNKNOWN"]}')
    monkeypatch.setattr(api,'Database',lambda *a,**k:pytest.fail('Invalid configuration connected to database'))
    with pytest.raises(PCCError) as exc:
        api.compare(CompareRequest(bundle.path,'old','new',str(tmp_path/'bad'),rules_config=str(path)),profiles=profiles)
    assert exc.value.code=='RULE_CONFIG_INVALID'


def test_offline_version_selection_ignores_unversioned_skipped_observations(tmp_path, online, monkeypatch):
    bundle, profiles, _, capture = online
    profiles['new']['expected_version'] = 'v8.5.7'
    def mixed(q, side, profile, **kwargs):
        if q.sql_text == 'SELECT 0':
            return PlanSnapshot(q.query_id, side, capture_status='skipped')
        return capture(q, side, profile, **kwargs)
    monkeypatch.setattr(api, 'capture_plan', mixed)
    original = api.compare(CompareRequest(bundle.path, 'old', 'new', str(tmp_path/'mixed')), profiles=profiles)
    offline = api.diff(DiffRequest(original.output_dir, str(tmp_path/'offline-mixed')))
    assert original.summary['not_run'] == '1' and offline.items == original.items
    assert offline.assessment_policy == original.assessment_policy
    assert offline.assessment_policy['profile_id'] == 'tidb-6.5.12-to-8.5.7'
    assert offline.assessment_policy['actual_versions'] == {'old': '6.5.12', 'new': '8.5.7'}


def test_rule_facts_and_groups_are_core_artifacts_and_replay_identically(tmp_path, monkeypatch, online):
    bundle, profiles, _, _ = online
    def capture(q, side, profile, **kwargs):
        new = side == 'new'
        reader, scan = ('TableReader', 'TableRangeScan') if new else ('IndexReader', 'IndexRangeScan')
        return PlanSnapshot(q.query_id, side, version=profile['expected_version'],
                            columns=['id', 'estRows', 'task', 'access object', 'operator info'],
                            rows=[[reader+'_1', '10', 'root', '', ('data:' if new else 'index:')+scan+'_2'],
                                  ['└─'+scan+'_2', '10', 'cop[tikv]', 'table:t'+('' if new else ', index:i(id)'), '']])
    monkeypatch.setattr(api, 'capture_plan', capture)
    result = api.compare(CompareRequest(bundle.path, 'old', 'new', str(tmp_path / 'rules')), profiles=profiles)
    offline = api.diff(DiffRequest(result.output_dir, str(tmp_path / 'offline-rules')))
    assert result.summary['plan_findings'] == '4' and result.summary['changed'] == '4'
    assert result.items == offline.items and result.diffs == offline.diffs
    for name in ('findings.jsonl', 'similar_groups.json'):
        assert (tmp_path / 'rules' / name).read_bytes() == (tmp_path / 'offline-rules' / name).read_bytes()
        assert name in verify(tmp_path / 'rules')['files']
    groups = json.loads((tmp_path / 'rules/similar_groups.json').read_text())
    assert len(groups) == 1 and len(groups[0]['query_ids']) == 4
    assert groups[0]['automatic_approval'] is False


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


@pytest.mark.parametrize("prior_assessment", ["3", "4", "5", "6"])
@pytest.mark.parametrize("resumed_concurrency", [1, 8])
def test_interrupt_and_resume(tmp_path, monkeypatch, online, prior_assessment, resumed_concurrency):
    bundle, profiles, calls, capture = online
    cancel = threading.Event()
    def interrupting(q, side, profile, **kwargs):
        result = capture(q, side, profile, **kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(api, "capture_plan", interrupting)
    output = str(tmp_path / "result")
    with monkeypatch.context() as old_build:
        old_build.setattr(api, "VERSIONS", {**api.VERSIONS, "assessment_version": prior_assessment})
        interrupted = api.compare(CompareRequest(bundle.path, "old", "new", output, concurrency=1), profiles=profiles, cancel=cancel)
    assert interrupted.exit_code == 4 and not (tmp_path / "result").exists()
    assert verify(tmp_path / ".result.partial")["run_status"] == "interrupted"
    monkeypatch.setattr(api, "capture_plan", capture)
    resumed = api.compare(CompareRequest(bundle.path, "old", "new", output, resume=True, concurrency=resumed_concurrency), profiles=profiles)
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


def test_resume_recaptures_legacy_partial_statistics(tmp_path, monkeypatch, online):
    bundle, profiles, calls, capture = online
    cancel = threading.Event()
    def cold_legacy(q, side, profile, **kwargs):
        snapshot = capture(q, side, profile, **kwargs)
        snapshot.rows[0][-1] = "stats:partial[id:unInitialized]"
        cancel.set()
        return snapshot
    monkeypatch.setattr(api, "capture_plan", cold_legacy)
    request = CompareRequest(bundle.path, "old", "new", str(tmp_path / "legacy"), concurrency=1)
    with monkeypatch.context() as old_build:
        old_build.setattr(api, "VERSIONS", {**api.VERSIONS, "assessment_version": "6"})
        api.compare(request, profiles=profiles, cancel=cancel)
    assert len(calls) == 1
    cold_key = calls[0]
    monkeypatch.setattr(api, "capture_plan", capture)
    from dataclasses import replace
    result = api.compare(replace(request, resume=True), profiles=profiles)
    assert result.exit_code == 0 and result.summary["same"] == "4"
    assert len(calls) == 9 and calls.count(cold_key) == 2


@pytest.mark.parametrize("stats", ["pseudo", "partial[id:unInitialized]"])
def test_offline_rechecks_legacy_incomplete_statistics(tmp_path, monkeypatch, online, stats):
    bundle, profiles, _, capture = online
    def legacy_capture(q, side, profile, **kwargs):
        snapshot = capture(q, side, profile, **kwargs)
        snapshot.rows[0][-1] = "stats:" + stats
        return snapshot
    monkeypatch.setattr(api, "capture_plan", legacy_capture)
    original = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "legacy")), profiles=profiles)
    assert original.summary["same"] == "4"
    first = api.diff(DiffRequest(original.output_dir, str(tmp_path / "rechecked")))
    second = api.diff(DiffRequest(first.output_dir, str(tmp_path / "rechecked-again")))
    assert first.exit_code == 3 and first.summary["incomparable"] == "4"
    assert first.items == second.items and first.summary == second.summary
    for item in first.items:
        assert {(i["side"], i["code"]) for i in item["issues"]} == {("old", "STATS_NOT_READY"), ("new", "STATS_NOT_READY")}


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


def test_slow_old_does_not_block_new_and_progress_identifies_pending_query(tmp_path, monkeypatch, online):
    bundle, profiles, _, capture = online
    release = threading.Event()
    news, progress = [], []
    def uneven(q, side, profile, **kwargs):
        if side == "old":
            assert release.wait(4)
        return capture(q, side, profile, **kwargs)
    def event(e):
        if e["event"] == "plan_captured" and e["side"] == "new":
            news.append(e["query_id"])
        if e["event"] == "capture_progress":
            progress.append(e)
            if len(news) == 4 and e["active"] and e["completed"] == "4":
                release.set()
    monkeypatch.setattr(api, "capture_plan", uneven)
    monkeypatch.setattr(api, "PROGRESS_INTERVAL", 0.01)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "progress"), concurrency=1),
                         profiles=profiles, on_event=event)
    assert result.exit_code == 0 and len(news) == 4
    assert progress[-1]["completed"] == progress[-1]["total"] == "8"
    stalled = next(e for e in progress if e["completed"] == "4")
    assert [a["query_id"] for a in stalled["active"]] == [bundle.queries[0].query_id]


def test_worker_memory_error_keeps_partial_comparison_resumable(tmp_path, monkeypatch, online):
    bundle, profiles, _, capture = online
    out = str(tmp_path / "limited")
    req = CompareRequest(bundle.path, "old", "new", out, concurrency=1)
    def limited(q, side, profile, **kwargs):
        if q.query_id == bundle.queries[-1].query_id:
            raise MemoryError()
        return capture(q, side, profile, **kwargs)
    monkeypatch.setattr(api, "capture_plan", limited)
    with pytest.raises(PCCError) as err:
        api.compare(req, profiles=profiles)
    assert err.value.code == "CAPTURE_MEMORY_LIMIT"
    saved = list((tmp_path / ".limited.partial" / "captures").glob("*.json"))
    assert saved
    monkeypatch.setattr(api, "capture_plan", capture)
    result = api.compare(CompareRequest(bundle.path, "old", "new", out, resume=True, concurrency=1), profiles=profiles)
    assert result.exit_code == 0


def _observed(snapshot, stale, conn):
    snapshot.context = {"connection_id": str(conn), "session": {"time_zone": "+08:00"},
                        "declared_session": {"tidb_stats_load_sync_wait": 10000}, "server_node": [{"hostname": "n1"}]}
    snapshot.warnings = [{"Code": "1105", "Message": "sync load stats timeout"}] if stale else []
    snapshot.attempts = [{"attempt": 1, "columns": snapshot.columns, "rows": snapshot.rows, "warnings": snapshot.warnings}]
    if stale:
        snapshot.issues = [{"code": "STATS_NOT_READY"}]
    return snapshot


def test_cold_plans_yield_slots_and_only_the_affected_side_is_retried(tmp_path, monkeypatch, online):
    bundle, profiles, calls, capture = online
    ids = [q.query_id for q in bundle.queries]
    events = []
    def cold(q, side, profile, **options):
        assert options["stats_retries"] == 0
        snapshot = capture(q, side, profile, **options)
        count = calls.count((q.query_id, side))
        return _observed(snapshot, side == "old" and q.query_id == ids[0] and count == 1, count)
    monkeypatch.setattr(api, "capture_plan", cold)
    monkeypatch.setattr(api, "_stats_retry_delay", lambda n: 0)
    req = CompareRequest(bundle.path, "old", "new", str(tmp_path / "cold"), concurrency=1, stats_retries=5)
    result = api.compare(req, profiles=profiles, on_event=events.append)
    assert result.exit_code == 0
    assert [qid for qid, side in calls if side == "old"] == ids + [ids[0]]
    assert [qid for qid, side in calls if side == "new"] == ids
    assert sum(e["event"] == "plan_captured" for e in events) == 8
    assert [e["attempt"] for e in events if e["event"] == "stats_retry_deferred"] == ["1"]
    assert [e["completed_by_side"] for e in events if e["event"] == "capture_progress"][-1] == {"old": "4", "new": "4"}
    plan = next(map(json.loads, (tmp_path / "cold/plans/old.jsonl").read_text().splitlines()))
    assert [a["attempt"] for a in plan["attempts"]] == ["1", "2"]
    assert [a["context"]["connection_id"] for a in plan["attempts"]] == ["1", "2"]
    assert plan["attempts"][0]["warnings"] and not plan["warnings"]
    offline = api.diff(DiffRequest(result.output_dir, str(tmp_path / "offline-cold")))
    assert offline.items == result.items and offline.summary == result.summary


def test_material_checks_overlap_across_targets_and_finish_before_capture(tmp_path, monkeypatch, online):
    bundle, profiles, _, capture = online
    rendezvous = threading.Barrier(2, timeout=3)
    checks = {"old": 0, "new": 0}
    def verify_both(db, manifest):
        checks[db.profile["host"]] += 1
        rendezvous.wait()
        return {}, []
    def capture_after_checks(q, side, profile, **kwargs):
        assert checks == {"old": 1, "new": 1}
        return capture(q, side, profile, **kwargs)
    monkeypatch.setattr(api, "verify_materials", verify_both)
    monkeypatch.setattr(api, "capture_plan", capture_after_checks)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "checked")), profiles=profiles)
    assert result.exit_code == 0 and checks == {"old": 2, "new": 2}


@pytest.mark.parametrize("budget", [0, 2])
def test_persistent_pseudo_stays_incomparable_at_original_retry_budget(tmp_path, monkeypatch, online, budget):
    bundle, profiles, calls, capture = online
    key = (bundle.queries[0].query_id, "old")
    def stale(q, side, profile, **options):
        snap = capture(q, side, profile, **options)
        return _observed(snap, (q.query_id, side) == key, calls.count((q.query_id, side)))
    monkeypatch.setattr(api, "capture_plan", stale)
    monkeypatch.setattr(api, "_stats_retry_delay", lambda n: 0)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "limit"), stats_retries=budget), profiles=profiles)
    assert calls.count(key) == budget + 1
    assert result.exit_code == 3 and result.summary["incomparable"] == "1"
    assert any(i["code"] == "STATS_NOT_READY" and i["side"] == "old" for i in result.issues)
    raw = next(map(json.loads, (tmp_path / "limit/plans/old.jsonl").read_text().splitlines()))
    assert len(raw["attempts"]) == budget + 1 and all(a["warnings"] for a in raw["attempts"])


@pytest.mark.parametrize("tamper", [False, True])
def test_pending_stats_survive_interrupt_and_preserve_budget(tmp_path, monkeypatch, online, tamper):
    bundle, profiles, calls, capture = online
    key = (bundle.queries[0].query_id, "old")
    cancel = threading.Event()
    def stale(q, side, profile, **options):
        snap = capture(q, side, profile, **options)
        return _observed(snap, (q.query_id, side) == key, calls.count((q.query_id, side)))
    def stop_when_saved(event):
        if event["event"] == "stats_retry_deferred":
            cancel.set()
    monkeypatch.setattr(api, "capture_plan", stale)
    monkeypatch.setattr(api, "_stats_retry_delay", lambda n: 0)
    out = str(tmp_path / "resume-stats")
    req = CompareRequest(bundle.path, "old", "new", out, stats_retries=2, concurrency=1)
    result = api.compare(req, profiles=profiles, on_event=stop_when_saved, cancel=cancel)
    assert result.exit_code == 4 and calls.count(key) == 1
    assert any(i["code"] == "STATS_RETRY_PENDING" for i in result.issues)
    partial = tmp_path / ".resume-stats.partial"
    assert not list((partial / "captures").glob(key[0] + ".old.*"))
    pending = next((partial / "stats-retries").glob("*.json"))
    if tamper:
        pending.write_bytes(pending.read_bytes().replace(b"sync load", b"modified"))
        with pytest.raises(PCCError) as error:
            api.compare(CompareRequest(bundle.path, "old", "new", out, stats_retries=2, resume=True), profiles=profiles)
        assert error.value.code == "HASH_MISMATCH"
        return
    resumed = api.compare(CompareRequest(bundle.path, "old", "new", out, stats_retries=2, resume=True), profiles=profiles)
    assert resumed.summary["incomparable"] == "1" and calls.count(key) == 3
    assert calls.count((key[0], "new")) == 1
    assert not any(i["code"] == "STATS_RETRY_PENDING" for i in resumed.issues)


def test_retried_connection_cannot_hide_drift_or_retry_unrelated_errors(tmp_path, monkeypatch, online):
    bundle, profiles, calls, capture = online
    ids = [q.query_id for q in bundle.queries]
    def changed(q, side, profile, **options):
        snap = capture(q, side, profile, **options)
        count = calls.count((q.query_id, side))
        _observed(snap, side == "old" and q.query_id in ids[:2] and count == 1, count)
        if side == "old" and q.query_id == ids[0] and count == 2:
            snap.context["server_node"] = [{"hostname": "other"}]
        if side == "old" and q.query_id == ids[1]:
            snap.issues.append({"code": "CONTEXT_DRIFT"})
        return snap
    monkeypatch.setattr(api, "capture_plan", changed)
    monkeypatch.setattr(api, "_stats_retry_delay", lambda n: 0)
    result = api.compare(CompareRequest(bundle.path, "old", "new", str(tmp_path / "drift-retry")), profiles=profiles)
    assert result.summary["incomparable"] == "2"
    assert calls.count((ids[0], "old")) == 2 and calls.count((ids[1], "old")) == 1
    assert all(any(i["code"] == "CONTEXT_DRIFT" for i in it["issues"]) for it in result.items[:2])
