import pytest

from pcc_tool import api
from pcc_tool.models import QueryCase
from pcc_tool.plan.rule_config import load_policy


def compare(snapshot, old_rows, new_rows, rules, *, sql="SELECT a FROM t", issues=(), pct=10):
    q = QueryCase.create(sql, "app")
    pair = {(q.query_id, side): snapshot(rows, query_id=q.query_id, side=side)
            for side, rows in (("old", old_rows), ("new", new_rows))}
    policy = load_policy(archived={"text_equivalence_rules": rules, "thresholds": {"estimate_pct": pct}})
    return api._summarize([q], pair, "fp", list(issues), policy=policy)[0]


def projection(attr="app.t.a", rows="10"):
    return [["Projection_1", rows, "root", "", attr], ["└─TableFullScan_2", "10", "cop[tikv]", "table:t", "keep order:false"]]


def test_only_enabled_and_fully_covered_changes_leave_the_diff_queue(snapshot):
    old, new = projection(), projection("app.t.a->Column#4", "11")
    for rules in ([], ["projection_alias"], ["estimate_tolerance"]):
        result = compare(snapshot, old, new, rules)
        assert result.exit_code == 1 and len(result.diffs) == 2
        assert result.items[0]["assessment_evidence"]["scope"] == "changed"
        assert "text_equivalence" not in result.items[0]
    result = compare(snapshot, old, new, ["projection_alias", "estimate_tolerance"])
    item = result.items[0]
    assert result.exit_code == 0 and result.summary["same"] == result.summary["text_equivalent"] == "1"
    assert not result.diffs and not item["findings"] and item["assessment"]["status"] == "excluded"
    assert [(m["rule"], m["old"], m["new"]) for m in item["text_equivalence"]] == [
        ("projection_alias", "app.t.a", "app.t.a->Column#4"), ("estimate_tolerance", "10", "11")]
    assert not item["assessment_evidence"]["matches"]


@pytest.mark.parametrize("before,after,pct,expected", [
    ("100", "110", 10, "same"), ("110", "100", 10, "same"), ("100", "110.0001", 10, "changed"),
    ("100", "101", 1, "same"), ("0", "1", 50, "changed"), ("1", None, 50, "changed"),
])
def test_estimate_tolerance_boundaries(snapshot, before, after, pct, expected):
    result = compare(snapshot, projection(rows=before), projection(rows=after), ["estimate_tolerance"], pct=pct)
    assert result.items[0]["compare_status"] == expected


@pytest.mark.parametrize("before,after,expected", [
    ("and(eq(1, 2), lt(3, 4))", "gt(4, 3), eq(2, 1)", "same"),
    ("lt(1, 2)", "gt(1, 2)", "changed"), ("eq('1', 2)", "eq(2, '1')", "changed"),
    ("eq(app.t.a, 2)", "eq(2, app.t.a)", "changed"),
])
def test_selection_constant_predicates_preserve_direction_and_types(snapshot, before, after, expected):
    def selection(value):
        return [["Selection_1", "1", "root", "", value]]
    result = compare(snapshot, selection(before), selection(after), ["integer_predicates"])
    assert result.items[0]["compare_status"] == expected


@pytest.mark.parametrize("change", ["order", "child", "engine", "source", "literal", "intermediate", "auxiliary_root", "context"])
def test_text_equivalence_requires_the_entire_ordered_plan(snapshot, change):
    old, new = projection(), projection("app.t.a->Column#4")
    issues = []
    if change == "order":
        old[0][-1], new[0][-1] = "app.t.a, app.t.b", "app.t.b->Column#3, app.t.a->Column#4"
    elif change == "child":
        new[1][3] = "table:other"
    elif change == "engine":
        new[1][2] = "cop[tiflash]"
    elif change == "source":
        new[0][-1] = "app.t.b->Column#4"
    elif change == "literal":
        old[0][-1], new[0][-1] = "'app.t.a'", "'app.t.a->Column#4'"
    elif change == "intermediate":
        for rows in (old, new):
            rows[0][0], rows[1][0] = "└─Projection_1", "  └─TableFullScan_2"
            rows.insert(0, ["HashAgg_3", "1", "root", "", "funcs:count(Column#4)"])
    elif change == "auxiliary_root":
        for rows in (old, new):
            rows.insert(0, ["CTE_3", "1", "root", "", ""])
    else:
        issues = [{"code": "STATS_NOT_READY"}]
    result = compare(snapshot, old, new, ["projection_alias"], issues=issues)
    assert result.items[0]["compare_status"] == ("incomparable" if issues else "changed")
    assert not result.items[0].get("text_equivalence")


def test_estimate_tolerance_preserves_identified_child_reordering(snapshot):
    def union(first, second):
        return [["Union_1", "210", "root", "", ""],
                ["├─TableFullScan_2", first, "cop[tikv]", "table:t", ""],
                ["└─TableFullScan_3", second, "cop[tikv]", "table:t", ""]]
    result = compare(snapshot, union("100", "110"), union("110", "100"), ["estimate_tolerance"])
    assert result.items[0]["compare_status"] == "changed"
    assert [e.type for e in result.diffs] == ["child_order_changed"]


def test_text_equivalence_preserves_mandatory_data_review(snapshot):
    result = compare(snapshot, projection(), projection("app.t.a->Column#9"), ["projection_alias"],
                     sql="SELECT (SELECT MAX(id) FROM t) FROM t")
    item = result.items[0]
    assert item["compare_status"] == "same" and item["requires_review"]
    assert item["assessment"]["status"] == "review" and item["assessment_evidence"]["mandatory_reviews"]
