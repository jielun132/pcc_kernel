import copy
import json
from pathlib import Path

import pytest

from pcc_tool.collector.dependencies import stats_fingerprint
from pcc_tool.collector.summary import merge_snapshots
from pcc_tool.models import PlanSnapshot
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures/live-plans-v719-03.json").read_text())


@pytest.mark.parametrize("case", FIXTURES["cases"], ids=lambda c: c["name"] + "-" + c["snapshot"]["format"])
def test_real_union_cte_aggregation_join_window(case):
    plan = parse_plan(PlanSnapshot(**case["snapshot"]))
    assert plan.parse_status == "ok", plan.issues
    assert plan.roots


@pytest.mark.parametrize("name", sorted({c["name"] for c in FIXTURES["cases"]}))
def test_real_row_json_equivalence(name):
    cases = [c for c in FIXTURES["cases"] if c["name"] == name]
    a, b = [parse_plan(PlanSnapshot(**c["snapshot"])) for c in cases]
    assert compare_ir(a, b) == ([], False)


def test_null_schema_and_plan_digest(summary_row):
    rows = [summary_row(schema_name=None, plan_digest=None, query_sample_text="SELECT 1"),
            summary_row(schema_name="app", plan_digest="plan")]
    for row in rows:
        row.pop("previous_statement_key", None)
    merged, issues = merge_snapshots(rows)
    assert len(merged) == 2 and not issues
    assert {r["schema_name"] for r in merged} == {"", "app"}
    assert all(r["previous_statement_key"] for r in merged)


def test_stats_fingerprint_keeps_distribution_but_excludes_usage_metadata():
    a = {"database_name": "app", "table_name": "t", "version": 1, "predicate_columns": [{"id": 1, "last_used_at": None}],
         "columns": {"a": {"last_update_version": 2, "stats_ver": 2, "histogram": {"ndv": 4, "buckets": [1, 2]}}},
         "count": 100, "modify_count": 0, "partitions": None, "is_historical_stats": False}
    b = copy.deepcopy(a)
    b["version"] = 99
    b["predicate_columns"] = [{"id": 1, "last_used_at": "2026-10-11"}]
    b["columns"]["a"]["last_update_version"] = 100
    assert stats_fingerprint(a) == stats_fingerprint(b)
    b["columns"]["a"]["histogram"]["buckets"][0] = 9
    assert stats_fingerprint(a) != stats_fingerprint(b)


def test_partition_fm_sketch_set_order_is_not_drift():
    a = {"partitions": {"p0": {"columns": {"id": {"fm_sketch": {"mask": 0, "hashset": [2, 1, 9007199254740993]},
                                                  "histogram": {"buckets": [1, 2]}}}}}}
    b = copy.deepcopy(a)
    b["partitions"]["p0"]["columns"]["id"]["fm_sketch"]["hashset"].reverse()
    assert stats_fingerprint(a) == stats_fingerprint(b)
    b["partitions"]["p0"]["columns"]["id"]["fm_sketch"]["hashset"][0] += 1
    assert stats_fingerprint(a) != stats_fingerprint(b)
    b = copy.deepcopy(a)
    b["count"] = 101
    assert stats_fingerprint(a) != stats_fingerprint(b)


def test_repeated_branch_ids_change_without_losing_reference_identity(snapshot):
    rows = [["Union_1", "2", "root", "", ""],
            ["├─TableReader_2", "1", "root", "", "data:TableRangeScan_3"],
            ["│ └─TableRangeScan_3", "1", "cop[tikv]", "table:t", "range:[1,1]"],
            ["└─TableReader_4", "1", "root", "", "data:TableRangeScan_5"],
            ["  └─TableRangeScan_5", "1", "cop[tikv]", "table:t", "range:[2,2]"]]
    import re
    changed_ids = [[re.sub(r"_(\d+)", lambda m: "_" + str(int(m[1]) + 90), v) for v in r] for r in rows]
    a, b = parse_plan(snapshot(rows)), parse_plan(snapshot(changed_ids))
    assert a.parse_status == b.parse_status == "ok"
    assert compare_ir(a, b) == ([], False)
    changed_ids[1][4] = "data:TableRangeScan_95"
    events, review = compare_ir(a, parse_plan(snapshot(changed_ids)))
    assert not review and any(e.type == "predicate_or_attribute_changed" for e in events)


def test_access_path_replacement_is_not_ambiguous(snapshot):
    a = [["TableReader_1", "10", "root", "", "data:Selection_2"],
         ["└─Selection_2", "10", "cop[tikv]", "", "or(eq(t.a, 1), eq(t.b, 2))"],
         ["  └─TableFullScan_3", "100", "cop[tikv]", "table:t", "keep order:false"]]
    b = [["IndexMerge_4", "10", "root", "", "type: union"],
         ["├─IndexRangeScan_5(Build)", "5", "cop[tikv]", "table:t, index:a(a)", "range:[1,1]"],
         ["├─IndexRangeScan_6(Build)", "5", "cop[tikv]", "table:t, index:b(b)", "range:[2,2]"],
         ["└─TableRowIDScan_7(Probe)", "10", "cop[tikv]", "table:t", "keep order:false"]]
    events, review = compare_ir(parse_plan(snapshot(a)), parse_plan(snapshot(b)))
    assert not review
    assert {"operator_changed", "node_added", "node_removed"} <= {e.type for e in events}
