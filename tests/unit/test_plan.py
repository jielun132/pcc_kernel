import copy
import json
from pathlib import Path

import pytest

from pcc_tool.models import PlanSnapshot
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan


def test_number_and_id_noise(snapshot):
    a = snapshot([["TableReader_7", "10", "root", "", "data:TableFullScan_6"],
                  ["└─TableFullScan_6", "10.00", "cop[tikv]", "table:t", "keep order:false"]])
    b = snapshot([["TableReader_99", "10.00", "root", "", "data:TableFullScan_100"],
                  ["└─TableFullScan_100", "10", "cop[tikv]", "table:t", "keep order:false"]], side="new")
    assert compare_ir(parse_plan(a), parse_plan(b)) == ([], False)


@pytest.mark.parametrize("before,after,expected,should_not_fire", [
    (["TableFullScan_1", "10", "cop[tikv]", "table:t", "keep order:false"],
     ["TableRangeScan_9", "10", "cop[tikv]", "table:t", "range:[1,3], keep order:false"],
     {"operator_changed", "access_path_changed", "predicate_or_attribute_changed"}, {"estimate_changed"}),
    (["Point_Get_1", "1", "root", "table:t", "handle:1"],
     ["Batch_Point_Get_2", "2", "root", "table:t", "handle:[1 2]"],
     {"operator_changed", "access_path_changed", "estimate_changed", "predicate_or_attribute_changed"}, {"ambiguous_match"}),
    (["IndexRangeScan_1", "10", "cop[tikv]", "table:t, index:a(a)", "range:[1,3]"],
     ["IndexRangeScan_2", "10", "cop[tikv]", "table:t, index:b(b)", "range:[1,3]"],
     {"access_path_changed"}, {"operator_changed", "estimate_changed"}),
    (["TableFullScan_1", "10", "cop[tikv]", "table:t, partition:p0", ""],
     ["TableFullScan_2", "10", "mpp[tiflash]", "table:t, partition:p1", ""],
     {"access_path_changed", "task_or_engine_changed"}, {"operator_changed"}),
    (["Selection_1", "0", "root", "", "eq(t.a, 10)"],
     ["Selection_2", "3", "root", "", "eq(t.a, 20)"],
     {"predicate_or_attribute_changed", "estimate_changed"}, {"operator_changed"}),
])
def test_golden_events(snapshot, before, after, expected, should_not_fire):
    events, review = compare_ir(parse_plan(snapshot([before])), parse_plan(snapshot([after], side="new")))
    fired = {e.type for e in events}
    assert expected <= fired
    assert not should_not_fire & fired
    assert not review


def test_child_order_build_probe_and_join(snapshot):
    a = [["HashJoin_1", "10", "root", "", "inner join"],
         ["├─TableFullScan_2(Build)", "10", "cop[tikv]", "table:a", ""],
         ["└─TableFullScan_3(Probe)", "10", "cop[tikv]", "table:b", ""]]
    b = [["HashJoin_4", "10", "root", "", "inner join"],
         ["├─TableFullScan_5(Build)", "10", "cop[tikv]", "table:b", ""],
         ["└─TableFullScan_6(Probe)", "10", "cop[tikv]", "table:a", ""]]
    events, review = compare_ir(parse_plan(snapshot(a)), parse_plan(snapshot(b)))
    assert {"child_order_changed", "join_changed"} <= {e.type for e in events}
    assert "access_path_changed" not in {e.type for e in events}
    assert not review


def test_ambiguous_self_join(snapshot):
    a = [["HashJoin_1", "10", "root", "", "inner join"],
         ["├─TableFullScan_2", "10", "cop[tikv]", "table:t", ""],
         ["└─TableFullScan_3", "10", "cop[tikv]", "table:t", ""]]
    b = copy.deepcopy(a)
    b[1][1] = "12"
    events, review = compare_ir(parse_plan(snapshot(a)), parse_plan(snapshot(b)))
    assert review and "ambiguous_match" in {e.type for e in events}
    assert compare_ir(parse_plan(snapshot(a)), parse_plan(snapshot(a))) == ([], False)


def test_json_and_row_same(snapshot):
    # Documented TiDB JSON shape; fixture provenance is recorded in fixtures/README.md.
    raw = [{"id": "Projection_4", "estRows": "10.00", "taskType": "root", "operatorInfo": "test.t.id", "subOperators": [
        {"id": "IndexReader_6", "estRows": "10.00", "taskType": "root", "operatorInfo": "index:IndexRangeScan_5", "subOperators": [
            {"id": "IndexRangeScan_5", "estRows": "10.00", "taskType": "cop[tikv]", "accessObject": "table:t, index:a(a)",
             "operatorInfo": "range:[1,1], keep order:false, stats:pseudo"}]}]}]
    json_ir = parse_plan(PlanSnapshot("q", "old", format="tidb_json", columns=["TiDB_JSON"], rows=[[json.dumps(raw)]]))
    row_ir = parse_plan(snapshot([["Projection_99", "10", "root", "", "test.t.id"],
        ["└─IndexReader_90", "10", "root", "", "index:IndexRangeScan_89"],
        ["  └─IndexRangeScan_89", "10", "cop[tikv]", "table:t, index:a(a)", "range:[1,1], keep order:false, stats:pseudo"]]))
    assert json_ir.parse_status == "ok"
    assert compare_ir(json_ir, row_ir) == ([], False)


def test_dml_na_and_multi_root(snapshot):
    p = parse_plan(snapshot([["Delete_1", "N/A", "root", "", "N/A"], ["└─TableDual_2", "0", "root", "", "rows:0"],
                             ["CTE_3", "1", "root", "", ""]]))
    assert p.parse_status == "ok" and len(p.roots) == 2
    assert p.roots[0].estimate is None


def test_unknown_fields_and_operators(snapshot):
    a = snapshot([["FutureOperator_1", "1", "root", "", ""]])
    assert parse_plan(a).parse_status == "partial"
    a = snapshot([{"id": "TableDual_1", "estRows": "1", "task": "root", "operator info": "", "future": "x"}])
    b = copy.deepcopy(a)
    b.rows[0]["future"] = "y"
    events, review = compare_ir(parse_plan(a), parse_plan(b))
    assert review and "unknown_field_changed" in {e.type for e in events}


@pytest.mark.parametrize("rows", [[], [["    └─TableDual_1", "1", "root", "", ""]],
    [["TableDual_1", "NaN", "root", "", ""]], [["TableDual_1", "Infinity", "root", "", ""]]])
def test_bad_plan_fails(snapshot, rows):
    assert parse_plan(snapshot(rows)).parse_status == "failed"


@pytest.mark.parametrize("operator", ["IndexMerge", "Apply", "CTE", "Sort", "TopN", "Limit", "HashAgg", "StreamAgg",
                                     "ExchangeSender", "ExchangeReceiver", "Window", "Update", "UnionAll"])
def test_operator_names_preserved(snapshot, operator):
    ir = parse_plan(snapshot([[operator + "_10", "N/A", "root", "", ""]]))
    assert ir.roots[0].operator == operator and ir.parse_status == "ok"


def test_literal_that_looks_like_operator_id_is_not_erased(snapshot):
    old = [["Selection_1", "1", "root", "", "eq(t.s, 'TableFullScan_2')"],
           ["└─TableFullScan_2", "10", "cop[tikv]", "table:t", ""]]
    new = [["Selection_8", "1", "root", "", "eq(t.s, 'TableFullScan_9')"],
           ["└─TableFullScan_9", "10", "cop[tikv]", "table:t", ""]]
    events, _ = compare_ir(parse_plan(snapshot(old)), parse_plan(snapshot(new)))
    assert {e.type for e in events} == {"predicate_or_attribute_changed"}


def test_unknown_operator_info_is_partial(snapshot):
    plan = parse_plan(snapshot([["TableDual_1", "1", "root", "", "future_option:enabled"]]))
    assert plan.parse_status == "partial"


def test_high_precision_estimate(snapshot):
    value = "1234567890123456789012345678901234567890.123"
    plan = parse_plan(snapshot([["TableDual_1", value, "root", "", ""]]))
    assert plan.roots[0].estimate == value


def test_inserted_wrapper_is_added_not_cascading_operator_changes(snapshot):
    old = [["TableReader_1", "10", "root", "", "data:Selection_2"],
           ["└─Selection_2", "10", "cop[tikv]", "", "eq(t.id, 1)"],
           ["  └─TableFullScan_3", "20", "cop[tikv]", "table:t", "keep order:false"]]
    new = [["Projection_7", "10", "root", "", "t.id"],
           ["└─TableReader_8", "10", "root", "", "data:Selection_9"],
           ["  └─Selection_9", "10", "cop[tikv]", "", "eq(t.id, 1)"],
           ["    └─TableFullScan_10", "20", "cop[tikv]", "table:t", "keep order:false"]]
    events, review = compare_ir(parse_plan(snapshot(old)), parse_plan(snapshot(new)))
    assert {e.type for e in events} == {"node_added"} and not review
    events, review = compare_ir(parse_plan(snapshot(new)), parse_plan(snapshot(old)))
    assert {e.type for e in events} == {"node_removed"} and not review
