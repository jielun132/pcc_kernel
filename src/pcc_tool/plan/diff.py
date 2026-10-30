from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from pcc_tool.models import DiffEvent, PlanIR, PlanNode, canonical, digest, primitive


def shape(node: PlanNode):
    return (node.operator, node.task, node.engine, node.access, node.attributes, node.estimate,
            node.role, node.unknown, [shape(child) for child in node.children])


def objects(node: PlanNode) -> tuple:
    own = re.search(r"(?:^|,\s*)table:([^,]+)", node.access)
    if own:
        return (own[1].strip(),)
    return tuple(sorted(v for child in node.children for v in objects(child)))


def descendant_key(node: PlanNode):
    """Anchor a wrapper through its descendants without using estimated row counts.

    A Projection's display attributes can change across versions while distinct
    UNION branches still have exactly matching range predicates below it. Require
    a unique, full ordered descendant signature on both sides; never pair by row.
    """
    def semantic(child):
        return (child.operator, child.task, child.engine, child.access, child.attributes,
                child.role, child.unknown, [semantic(c) for c in child.children])
    return (node.operator, node.role, digest([semantic(c) for c in node.children])) if node.children else None


def compare_ir(old: PlanIR, new: PlanIR) -> tuple[list[DiffEvent], bool]:
    events = []
    correspondences = {}
    review = old.parse_status != "ok" or new.parse_status != "ok"

    def emit(kind, left, right, field, before, after, confidence="high", requires_review=False, **evidence):
        events.append(DiffEvent(old.query_id, kind, left.node_id if left else None,
                                right.node_id if right else None, field, primitive(before), primitive(after),
                                {"old": left.evidence if left else None, "new": right.evidence if right else None,
                                 "correspondence": correspondences.get((left.node_id, right.node_id)) if left and right else None,
                                 **evidence}, confidence, requires_review))

    def unmatched(node, added):
        emit("node_added" if added else "node_removed", None if added else node, node if added else None,
             "node", None if added else shape(node), shape(node) if added else None)
        for child in node.children:
            unmatched(child, added)

    def match_lists(left: list[PlanNode], right: list[PlanNode], parent_old=None, parent_new=None):
        nonlocal review
        # Equal ordered subtrees need no speculative pairing, including identical self-joins.
        if canonical([shape(n) for n in left]) == canonical([shape(n) for n in right]):
            return
        def unary_contains(wrapper, target):
            current = wrapper
            while len(current.children) == 1:
                current = current.children[0]
                if current.operator == target.operator and objects(current) == objects(target):
                    return True
            return False
        if len(left) == len(right) == 1 and left[0].operator != right[0].operator:
            if unary_contains(right[0], left[0]):
                emit("node_added", None, right[0], "node", None, {"operator": right[0].operator, "attributes": right[0].attributes})
                match_lists(left, right[0].children, parent_old, right[0])
                return
            if unary_contains(left[0], right[0]):
                emit("node_removed", left[0], None, "node", {"operator": left[0].operator, "attributes": left[0].attributes}, None)
                match_lists(left[0].children, right, left[0], parent_new)
                return
        li, ri, pairs = set(range(len(left))), set(range(len(right))), []
        key_functions = [lambda n: digest(shape(n)),
                         lambda n: (objects(n), n.operator, n.attributes),
                         descendant_key,
                         lambda n: objects(n) or None,
                         lambda n: (n.operator, n.role), lambda n: n.operator]
        methods = ("equal_subtree", "object_operator_attributes", "unique_descendants", "accessed_objects", "operator_role", "operator")
        for method, key in zip(methods, key_functions):
            lc, rc = Counter(key(left[i]) for i in li), Counter(key(right[i]) for i in ri)
            right_by_key = {key(right[j]): j for j in sorted(ri) if rc[key(right[j])] == 1}
            for i in sorted(li):
                token = key(left[i])
                if token is not None and lc[token] == 1 and token in right_by_key:
                    j = right_by_key[token]
                    pairs.append((i, j))
                    correspondences[left[i].node_id, right[j].node_id] = {
                        "method": method, "scope": [parent_old.node_id if parent_old else None,
                                                   parent_new.node_id if parent_new else None]}
                    li.remove(i)
                    ri.remove(j)
        if len(li) == len(ri) == 1:
            i, j = li.pop(), ri.pop()
            pairs.append((i, j))
            correspondences[left[i].node_id, right[j].node_id] = {
                "method": "sole_remaining_pair", "scope": [parent_old.node_id if parent_old else None,
                                                           parent_new.node_id if parent_new else None]}
        # A replacement access-path parent can have entirely different operator
        # families below it (e.g. TableReader/Selection -> IndexMerge/scans).
        # Report removals/additions without inventing an ambiguous correspondence.
        replacement = (parent_old is not None and parent_new is not None
                       and parent_old.operator != parent_new.operator
                       and {left[i].operator for i in li}.isdisjoint({right[j].operator for j in ri}))
        if li and ri and not replacement:
            review = True
            emit("ambiguous_match", parent_old, parent_new, "children",
                 [left[i].node_id for i in sorted(li)], [right[i].node_id for i in sorted(ri)],
                 "low", True)
        ordered = sorted(pairs)
        if [j for _, j in ordered] != sorted(j for _, j in ordered):
            emit("child_order_changed", parent_old, parent_new, "child_order",
                 [left[i].node_id for i, _ in ordered], [right[j].node_id for _, j in ordered])
        for i, j in ordered:
            visit(left[i], right[j])
        for i in sorted(li):
            unmatched(left[i], False)
        for j in sorted(ri):
            unmatched(right[j], True)

    def visit(a: PlanNode, b: PlanNode):
        if a.operator != b.operator:
            emit("operator_changed", a, b, "operator", a.operator, b.operator)
        if a.access != b.access or (a.operator != b.operator and ("Scan" in a.operator or "Get" in a.operator or "Get" in b.operator)):
            emit("access_path_changed", a, b, "access", {"operator": a.operator, "object": a.access},
                 {"operator": b.operator, "object": b.access})
        if (a.task, a.engine) != (b.task, b.engine):
            emit("task_or_engine_changed", a, b, "task/engine", [a.task, a.engine], [b.task, b.engine])
        if a.role != b.role or (("Join" in a.operator or a.operator == "Apply") and
                               (a.operator != b.operator or a.attributes != b.attributes)):
            emit("join_changed", a, b, "join/role", [a.operator, a.role, a.attributes], [b.operator, b.role, b.attributes])
        if a.attributes != b.attributes:
            emit("predicate_or_attribute_changed", a, b, "operator_info", a.attributes, b.attributes)
        if a.estimate != b.estimate:
            transition = "missing" if a.estimate is None or b.estimate is None else (
                "zero_to_positive" if Decimal(a.estimate) == 0 and Decimal(b.estimate) > 0 else "value_changed")
            emit("estimate_changed", a, b, "estRows", a.estimate, b.estimate, transition=transition)
        if a.unknown != b.unknown:
            emit("unknown_field_changed", a, b, "unknown", a.unknown, b.unknown, requires_review=True)
        match_lists(a.children, b.children, a, b)

    if old.parse_status != "failed" and new.parse_status != "failed":
        match_lists(old.roots, new.roots)
    return events, review
