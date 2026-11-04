from collections import Counter
import threading

import pytest

from pcc_tool import api
from pcc_tool.artifacts.captures import SavedPlans
from pcc_tool.capture.scheduling import WaitingQueries, query_tables
from pcc_tool.models import CompareRequest, PlanSnapshot, QueryCase


def sample(index, *tables):
    return QueryCase.create(f"SELECT {index}", "d", dependencies=[list(t) for t in tables])


A, B, C = ("d", "orders"), ("archive", "orders"), ("d", "customers")
OBJECTS = [dict(schema=s, table=t, kind="table") for s, t in (A, B, C)]


def test_rotates_tables_and_tracks_nested_views_and_unknowns():
    objects = OBJECTS + [
        dict(schema="d", table="v", kind="view", dependencies=[A]),
        dict(schema="d", table="nested", kind="view", dependencies=[("d", "v"), C]),
        dict(schema="d", table="unresolved", kind="view", dependencies=[A, ("d", "missing")]),
    ]
    qs = [sample(1, A), sample(2, A), sample(3, ("d", "nested")), sample(4, B), sample(5, ("d", "unresolved"))]
    tables = query_tables(qs, objects)
    assert tables[qs[2].query_id] == frozenset((A, C)) and not tables[qs[4].query_id]
    queue = WaitingQueries(tables, qs)
    assert queue.take() is qs[0]
    assert queue.take({A}) is qs[3]
    assert queue.take({A}) is None
    more = sample(6, B)
    tables[more.query_id] = frozenset((B,))
    queue.add(more)
    assert queue.take({A, C}) is more
    assert [queue.take(), queue.take(), queue.take()] == [qs[2], qs[4], qs[1]]
    assert len(queue) == 0


@pytest.mark.parametrize("count", [1, 2, 4, 8, 16])
def test_first_batch_fills_each_side_without_a_slow_timer(tmp_path, monkeypatch, count):
    qs = [sample(i, A) for i in range(count+2)]
    first = {q.query_id for q in qs[:count]}
    gate = threading.Barrier(count*2, timeout=5)
    lock = threading.Lock()
    active, peak, calls = Counter(), Counter(), Counter()
    def capture(q, side, profile, **options):
        with lock:
            calls[q.query_id, side] += 1
            active[side] += 1
            peak[side] = max(peak[side], active[side])
        try:
            options["on_stage"]("explain")
            if q.query_id in first:
                gate.wait()
            return PlanSnapshot(q.query_id, side)
        finally:
            with lock:
                active[side] -= 1
    monkeypatch.setattr(api, "capture_plan", capture)
    monkeypatch.setattr(api, "SLOW_EXPLAIN_SECONDS", 3600)
    snapshots = SavedPlans(tmp_path)
    api._capture_plans(qs, snapshots, {"old": {}, "new": {}}, "row",
                       CompareRequest("bundle","old","new","out", concurrency=count),
                       threading.Event(), api.Events(), OBJECTS)
    assert peak == {"old": count, "new": count}
    assert len(snapshots) == len(calls) == 2*len(qs)
    assert all(n==1 for n in calls.values())
    assert all(snapshots[k].capture_status=="ok" for k in snapshots)


def test_blocked_join_leaves_slots_for_other_tables_and_due_retry(tmp_path, monkeypatch):
    hot = [sample(i, A, C) for i in range(8)]
    separate = [sample(i+100, B) for i in range(3)]
    qs = hot + separate
    cold_ids = {q.query_id for q in separate}
    retry = separate[-1]
    snapshots = SavedPlans(tmp_path)
    for side in ("old", "new"):
        snapshots.defer(PlanSnapshot(retry.query_id, side, issues=[{"code":"STATS_NOT_READY"}],
                                     attempts=[{"attempt":1, "warnings": ["sync load stats timeout"]}]))
    release = {s:threading.Event() for s in ("old","new")}
    cold_done = {s:set() for s in release}
    def capture(q, side, profile, **options):
        options["on_stage"]("explain")
        if q.query_id == hot[0].query_id:
            assert release[side].wait(5)
        return PlanSnapshot(q.query_id, side, attempts=[{"attempt":1}])
    def event(e):
        if e["event"] == "plan_captured" and e["query_id"] in cold_ids:
            side = e["side"]
            cold_done[side].add(e["query_id"])
            if cold_done[side] == cold_ids:
                release[side].set()
    monkeypatch.setattr(api, "capture_plan", capture)
    monkeypatch.setattr(api, "SLOW_EXPLAIN_SECONDS", 0)
    monkeypatch.setattr(api, "_stats_retry_delay", lambda n: 0)
    api._capture_plans(qs, snapshots, {"old": {}, "new": {}}, "row", CompareRequest("bundle","old","new","out",concurrency=2),
                       threading.Event(), api.Events(event), OBJECTS)
    assert all(snapshots[k].capture_status=="ok" for k in snapshots)
    for side in release:
        assert release[side].is_set()
        assert len(snapshots[retry.query_id,side].attempts)==2


def test_first_wave_includes_separate_tables_before_any_result(tmp_path, monkeypatch):
    qs = [sample(i, A) for i in range(10)] + [sample(10, B), sample(11, C), sample(12, A, C)]
    gate = threading.Barrier(8,timeout=5)
    first = {"old": [], "new": []}
    lock = threading.Lock()
    def capture(q, side, profile, **options):
        with lock:
            first_wave = len(first[side]) < 4
            if first_wave:
                first[side].append(q.query_id)
        if first_wave:
            gate.wait()
        return PlanSnapshot(q.query_id, side)
    monkeypatch.setattr(api, "capture_plan", capture)
    monkeypatch.setattr(api, "SLOW_EXPLAIN_SECONDS", 3600)
    snapshots = SavedPlans(tmp_path)
    api._capture_plans(qs, snapshots, {"old": {}, "new": {}}, "row", CompareRequest("bundle","old","new","out",concurrency=4),
                       threading.Event(), api.Events(), OBJECTS)
    assert all(snapshots[k].capture_status=="ok" for k in snapshots)
    for batch in first.values():
        assert qs[10].query_id in batch and qs[11].query_id in batch
