"""Schema-and-statistics replay: positive shapes, negative controls and precedence."""
import pytest

from pcc_tool import api
from pcc_tool.models import PlanNode, QueryCase
from pcc_tool.plan.assessment import evaluate
from pcc_tool.plan.empty_replay import replay_reviews, view_evidence

SCHEMA = {"d.t": {"columns": {"id": "INT", "outer_only": "INT"}}, "d.s": {"columns": {"k": "INT", "id": "INT"}}}


def cards(sql, *, nodes=(), deps=(), schema=SCHEMA, parameters=None):
    q = QueryCase.create(sql, "d", dependencies=list(deps), parameters=parameters)
    return {r["rule_id"]: r for r in replay_reviews(q, {"old": list(nodes), "new": []}, schema)}


@pytest.mark.parametrize("sql, rule, text", [
    ("SELECT * FROM t WHERE id < (SELECT MAX(k) FROM s)", "D01", "MAX 在空集为 NULL"),
    ("SELECT (SELECT COUNT(*) FROM s) FROM t", "D01", "COUNT 在空集为 0"),
    ("SELECT * FROM t WHERE CASE WHEN id=1 THEN (SELECT k FROM s) ELSE 0 END > 0", "D01", "1242"),
    ("WITH x AS (SELECT k FROM s) SELECT * FROM t WHERE id=(SELECT MAX(k) FROM x)", "D01", "内层读取 s"),
    ("SELECT (SELECT MAX(x.k) FROM (SELECT k FROM s) x)", "D01", "内层读取 s"),
    ("SELECT (SELECT k FROM s UNION ALL SELECT id FROM t)", "D01", "内层读取 s、t"),
    ("SELECT * FROM t WHERE EXISTS (SELECT 1 FROM s WHERE k=5)", "D02", "空集为假"),
    ("SELECT * FROM t WHERE NOT EXISTS (SELECT 1 FROM s)", "D02", "空集为真"),
    ("SELECT * FROM t WHERE EXISTS (SELECT 1 FROM s WHERE k=t.id)", "D03", "引用外层"),
    ("SELECT * FROM t WHERE id=(SELECT MAX(k) FROM s WHERE k=outer_only)", "D03", "引用外层"),
    ("SELECT * FROM t WHERE id NOT IN (SELECT k FROM s)", "D04", "NOT IN"),
    ("SELECT * FROM t WHERE id>ALL (SELECT k FROM s)", "D04", "ALL"),
    ("SELECT * FROM t WHERE id=ANY (SELECT k FROM s)", "D04", "ANY"),
])
def test_sql_input_evidence(sql, rule, text):
    result = cards(sql)
    assert set(result) == {rule}
    assert text in result[rule]["reason"] and result[rule]["check"] and result[rule]["source"].startswith("https://")


@pytest.mark.parametrize("sql", [
    "SELECT (SELECT 1 FROM dual) FROM t",
    "WITH x AS (SELECT 2 AS n) SELECT id FROM t WHERE id=(SELECT n FROM x)",
    "SELECT * FROM t WHERE EXISTS (SELECT COUNT(*) FROM s)",
    "SELECT * FROM t WHERE id < (SELECT MAX(k) FROM s LIMIT 0)",
    "SELECT MAX(k) FROM s WHERE id=2", "SELECT * FROM t WHERE id IN (1,2,3)",
    "SELECT * FROM t WHERE 1=0", "SELECT * FROM t ORDER BY id LIMIT 2",
    "SELECT '(SELECT MAX(k) FROM s)' FROM t /* EXISTS (SELECT k FROM s) */",
    "SELECT * FROM (SELECT id FROM t) AS x", "SELECT 1 UNION SELECT 2",
    "SELECT (SELECT MAX(id) FROM t WHERE 1=0)", "SELECT EXISTS(SELECT 1 FROM t WHERE FALSE)",
])
def test_controls_are_not_held_just_for_shape(sql):
    assert not cards(sql, nodes=[PlanNode("1", "TableDual", attributes="rows:0")])


def test_prepared_and_view_dispatch():
    assert "D01" in cards("SELECT (SELECT MAX(k) FROM s WHERE id=?)", parameters=[{"type": "int", "value": "8"}])
    view = view_evidence("CREATE VIEW v AS SELECT * FROM t WHERE EXISTS (SELECT 1 FROM s)", "d", SCHEMA)
    meta = {**SCHEMA, "d.v": {"view_reviews": view}, "d.v2": {"view_dependencies": [["d", "v"]]}}
    held = cards("SELECT * FROM v2", deps=[["d", "v2"]], schema=meta)
    assert set(held) == {"D02"} and "视图 d.v" in held["D02"]["reason"]
    assert set(cards("SELECT * FROM v", deps=[["d", "v"]], schema={"d.v": {"columns": {}}})) == {"D10"}


@pytest.mark.parametrize("sql,expected,text", [
    ("UPDATE t SET id=(SELECT k FROM s LIMIT 1)", {"D01", "D11"}, "LIMIT 1"),
    ("INSERT INTO t SELECT id,id FROM s", {"D11"}, "写入行数"),
    ("REPLACE INTO t VALUES (1,2)", {"D11"}, "唯一键"),
    ("INSERT INTO t VALUES (1,2) ON DUPLICATE KEY UPDATE id=2", {"D11"}, "冲突"),
    ("SELECT * FROM t TABLESAMPLE REGIONS()", {"D12"}, "Region"),
    ("SELECT id FROM t ORDER BY RAND()", {"D12"}, "RAND"),
    ("SELECT id FROM t EXCEPT SELECT id FROM s", {"D04"}, "重叠率"),
    ("SELECT * FROM t CROSS JOIN s", {"D13"}, "匹配一行"),
])
def test_data_dependent_write_sample_and_multiplicity(sql, expected, text):
    result = cards(sql)
    assert set(result) == expected and any(text in r["reason"] for r in result.values())


@pytest.mark.parametrize("op, access, info, expected", [
    ("HashJoin", "", "runtime filter:0[IN] <- d.s.k", {"D06"}),
    ("TableReader", "partition:p0,p1", "data:TableFullScan", set()),
    ("TableFullScan", "", "PartitionTableScan:true", {"D07"}),
    ("IndexJoin", "", "inner join", {"D03"}),
    ("Window", "", "sum(d.t.id) over(partition by d.t.outer_only)", {"D09"}),
    ("CTEFullScan", "", "data:CTE_0", {"D09"}),
])
def test_visible_runtime_evidence(op, access, info, expected):
    assert set(cards("SELECT id FROM t", nodes=[PlanNode("1", op, access=access, attributes=info)])) == expected


def test_limit_early_stop_requires_a_candidate_filter_or_sort():
    sql = "SELECT id FROM t WHERE outer_only>2 ORDER BY id LIMIT 20"
    assert set(cards(sql, nodes=[PlanNode("1", "Selection")])) == {"D05"}
    assert not cards(sql, nodes=[PlanNode("1", "Point_Get")])
    assert not cards(sql.replace("20", "0"), nodes=[PlanNode("1", "Selection")])


def test_same_dual_is_reviewed_in_summary_with_a_per_side_reason(snapshot):
    q = QueryCase.create("SELECT id FROM t WHERE id < (SELECT MAX(k) FROM s)", "d")
    snaps = {(q.query_id, side): snapshot([["TableDual_3", "0", "root", "", "rows:0"]], side, q.query_id)
             for side in ("old", "new")}
    r, _ = api._summarize([q], snaps, "same-dual", [], assessment_schema=SCHEMA)
    item = r.items[0]
    assert item["compare_status"] == "same" and item["requires_review"] and r.exit_code == 0
    assert r.summary["data_review"] == {"same": "1", "changed": "0", "incomparable": "0"}
    e = item["assessment_evidence"]
    assert evaluate(e, []) == item["assessment"]
    assert item["assessment"]["status"] == "review" and not item["assessment"]["matched"]
    assert all(s in item["assessment"]["reasons"][0] for s in ("D01", "old", "new", "TableDual rows:0"))
    snaps[(q.query_id, "old")].capture_status = "failed"
    failed, _ = api._summarize([q], snaps, "failed-side", [], assessment_schema=SCHEMA)
    assert failed.items[0]["compare_status"] == "incomparable" and failed.items[0]["assessment"]["status"] == "review"
    assert failed.exit_code == 3


def test_same_limit_with_residual_is_advisory_until_selected(snapshot):
    q = QueryCase.create("SELECT id FROM t WHERE outer_only>2 ORDER BY id LIMIT 20", "d")
    rows = [["Limit_1", "20", "root", "", "offset:0, count:20"],
            ["└─TableReader_2", "20", "root", "", "data:Selection_3"],
            ["  └─Selection_3", "20", "cop[tikv]", "", "gt(d.t.outer_only, 2)"],
            ["    └─TableFullScan_4", "100", "cop[tikv]", "table:t", "keep order:true"]]
    snaps = {(q.query_id, s): snapshot(rows, s, q.query_id) for s in ("old", "new")}
    result, _ = api._summarize([q], snaps, "limit", [], assessment_schema=SCHEMA)
    item = result.items[0]
    assert item["compare_status"] == "same" and not item["requires_review"]
    e = item["assessment_evidence"]
    assert item["assessment"]["status"] == "excluded" and not e["mandatory_reviews"]
    assert [r["rule_id"] for r in e["runtime_notes"]] == ["D05"]
    assert sum(map(int, result.summary["review_basis"].values())) == 0
    assert evaluate(e, ["D05"])["status"] == "review"
    assert "D05" in evaluate(e, ["D05"])["reasons"][0] and evaluate(e, ["D03"])["status"] == "excluded"
