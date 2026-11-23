"""Opt-in whole-plan equivalence proofs over parsed EXPLAIN fields."""
from pcc_tool.plan.assessment import alias_only, guard_pass, simple_predicates


TEXT_RULES = {
    "projection_alias": "最终 Projection 的来源列和输出顺序相同，仅输出别名标记变化",
    "integer_predicates": "Selection 中的纯整数常量比较仅交换等价操作数或合取顺序",
    "estimate_tolerance": "对应节点的两个正 estRows 在配置的估算容差内",
}


def match_text_rules(old, new, events, policy):
    enabled = (policy or {}).get("text_equivalence_rules", ())
    if not enabled or old.parse_status != "ok" or new.parse_status != "ok" or len(old.roots) != len(new.roots):
        return []
    # A matcher can identify swapped siblings even if both estimates fit the
    # tolerance at their original positions. Preserve that structural evidence.
    if any(e.type not in {"predicate_or_attribute_changed", "estimate_changed"}
           or e.requires_review or e.old_node != e.new_node for e in events):
        return []
    matches = []

    def visit(a, b, root):
        if ((a.operator, a.task, a.engine, a.access, a.role, a.unknown, len(a.children))
                != (b.operator, b.task, b.engine, b.access, b.role, b.unknown, len(b.children))):
            return False
        if a.attributes != b.attributes:
            if root and "projection_alias" in enabled and alias_only(a, b):
                rule = "projection_alias"
            elif a.operator == "Selection" and "integer_predicates" in enabled:
                before = simple_predicates(a.attributes)
                if not before or before != simple_predicates(b.attributes):
                    return False
                rule = "integer_predicates"
            else:
                return False
            matches.append(dict(rule=rule, old_node=a.node_id, new_node=b.node_id,
                                field="operator_info", old=a.attributes, new=b.attributes))
        if a.estimate != b.estimate:
            guard = dict(kind="estimate", input=a.estimate, output=b.estimate, setting="estimate_pct")
            if "estimate_tolerance" not in enabled or not guard_pass(guard, policy["thresholds"]):
                return False
            matches.append(dict(rule="estimate_tolerance", old_node=a.node_id, new_node=b.node_id,
                                field="estRows", old=a.estimate, new=b.estimate))
        return all(visit(x, y, False) for x, y in zip(a.children, b.children))

    final_output = len(old.roots) == 1
    return matches if all(visit(a, b, final_output) for a, b in zip(old.roots, new.roots)) else []
