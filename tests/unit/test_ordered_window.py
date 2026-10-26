import copy
import json
from pathlib import Path

from pcc_tool.models import PlanSnapshot
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan


def test_live_ordered_window_keeps_operator_order_and_frame_semantics():
    evidence = json.loads((Path(__file__).parents[1] / "fixtures/live-ordered-window.json").read_text())
    plans = [parse_plan(PlanSnapshot(**snapshot)) for snapshot, _ in evidence["observations"]]
    assert all(p.parse_status == "ok" for p in plans)
    assert compare_ir(*plans) == ([], False)
    node = plans[0].roots[0].children[0].children[0]
    assert node.operator == "OrderedWindow" and "partition by" in node.attributes
    assert "order by" in node.attributes and "rows between" in node.attributes
    altered = copy.deepcopy(plans[0])
    altered.roots[0].children[0].children[0].operator = "Window"
    assert compare_ir(plans[0], altered)[0]
    altered = copy.deepcopy(plans[0])
    altered.roots[0].children[0].children[0].attributes = node.attributes.replace("current row", "unbounded preceding", 1)
    assert compare_ir(plans[0], altered)[0]
