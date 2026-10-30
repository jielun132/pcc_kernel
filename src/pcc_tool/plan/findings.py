"""Deterministic fact classification. Rules describe changes, never approve SQL."""
from collections import defaultdict
import re

from pcc_tool.models import digest

READERS = {"IndexReader", "TableReader", "IndexLookUp", "IndexMerge"}
SCANS = {"IndexRangeScan", "IndexFullScan", "TableRangeScan", "TableFullScan", "TableRowIDScan",
         "Point_Get", "Batch_Point_Get", "PointGet", "BatchPointGet", "TableSample"}
ACCESS = READERS | SCANS
RULES = {
    "ACCESS_SCAN": ("扫描方式变化", "对应访问节点的扫描或点查算子发生变化；保留具体算子名。"),
    "ACCESS_READER": ("父级访问链路变化", "对应访问链路的 Reader、LookUp 或 Merge 算子发生变化。"),
    "ACCESS_OBJECT": ("访问对象变化", "对应节点的表、索引或分区描述发生变化。"),
    "ACCESS_INDEX": ("索引使用情况变化", "同一访问链路中计划明确列出的索引发生变化；未列索引不等于不使用主键。"),
    "ACCESS_DETAIL": ("访问链路属性变化", "同一已变化访问链路上的范围、属性或节点增删，作为该项的补充事实。"),
    "JOIN": ("连接方式或角色变化", "对应节点的 Join 算法、Build/Probe 角色或连接属性发生变化。"),
    "ENGINE": ("任务或引擎变化", "对应节点的执行任务或存储引擎发生变化。"),
    "ORDER": ("子节点顺序变化", "可对应的子节点排列发生变化，不忽略 Join 顺序。"),
    "OPERATOR": ("算子变化", "对应节点的具体算子名发生变化。"),
    "ATTRIBUTE": ("谓词或属性变化", "对应节点的 operator info 发生变化，保留原值，不推断性能优劣。"),
    "STRUCTURE": ("节点增删", "计划存在新增或移除节点；未建立对应关系的节点不推测为同一节点。"),
    "ESTIMATE": ("估算行数变化", "对应节点的 estRows 不同；单列为辅助指标，不作为访问路径变化。"),
    "UNCLASSIFIED": ("待复核的观察", "匹配有歧义、未知字段或尚无专门规则的事件，保留原始证据。"),
}
CATEGORIES = {"access_path": "访问路径变化", "join": "连接方式或角色变化", "engine": "任务或引擎变化",
              "order": "子节点顺序变化", "operator": "算子变化", "attribute": "谓词或属性变化",
              "structure": "节点增删", "estimate": "估算行数变化", "unclassified": "待复核的观察"}


def _index(ir):
    nodes, owners, branches = {}, {}, defaultdict(list)

    def visit(node, reader=None, depth=0):
        if node.operator in READERS:
            reader = node.node_id
        owner = reader or (node.node_id if node.operator in SCANS else None)
        nodes[node.node_id] = node
        owners[node.node_id] = owner
        if owner:
            branches[owner].append((node, depth))
        for child in node.children:
            visit(child, reader, depth + 1)

    if ir:
        for node in ir.roots:
            visit(node)
    return nodes, owners, branches


def classify_findings(old, new, events, compare_status):
    """Merge only events in the same SQL/access occurrence; estimates stay separate."""
    indices = {"old": _index(old), "new": _index(new)}
    links = {"old": defaultdict(set), "new": defaultdict(set)}
    for event in events:
        a = indices["old"][1].get(event.old_node)
        b = indices["new"][1].get(event.new_node)
        if a and b:
            links["old"][a].add(b)
            links["new"][b].add(a)

    def chain(event):
        a = indices["old"][1].get(event.old_node)
        b = indices["new"][1].get(event.new_node)
        if a and not b and len(links["old"][a]) == 1:
            candidate = next(iter(links["old"][a]))
            if links["new"][candidate] == {a}:
                b = candidate
        if b and not a and len(links["new"][b]) == 1:
            candidate = next(iter(links["new"][b]))
            if links["old"][candidate] == {b}:
                a = candidate
        return (a, b) if a and b and links["old"][a] == {b} and links["new"][b] == {a} else None

    def access_rules(event):
        a = indices["old"][0].get(event.old_node)
        b = indices["new"][0].get(event.new_node)
        rules = []
        if a and b and a.operator in ACCESS and b.operator in ACCESS:
            if a.operator != b.operator:
                if a.operator in SCANS or b.operator in SCANS:
                    rules.append("ACCESS_SCAN")
                if a.operator in READERS or b.operator in READERS:
                    rules.append("ACCESS_READER")
            if a.access != b.access:
                rules.append("ACCESS_OBJECT")
        return rules

    def access_signature(side, root):
        return sorted((n.operator, n.access) for n, _ in indices[side][2].get(root, []) if n.operator in ACCESS)

    scopes = {chain(e) for e in events if chain(e)}
    access_changes = {scope for scope in scopes if access_signature("old", scope[0]) != access_signature("new", scope[1])}
    groups = {}
    join_pairs = {(e.old_node, e.new_node) for e in events if e.type == "join_changed"}
    for number, event in enumerate(events, 1):
        pair = (event.old_node, event.new_node)
        scope = chain(event)
        nodes = [indices[s][0].get(getattr(event, s + "_node")) for s in ("old", "new")]
        if event.type == "estimate_changed":
            category, rules = "estimate", ["ESTIMATE"]
        elif (scope in access_changes and any(n and n.operator in ACCESS for n in nodes)
              and event.type in {"operator_changed", "access_path_changed", "predicate_or_attribute_changed",
                                 "node_added", "node_removed"}):
            category, pair = "access_path", scope
            rules = access_rules(event) if event.type in {"operator_changed", "access_path_changed"} else []
            rules = rules or ["ACCESS_DETAIL"]
        elif pair in join_pairs and event.type in {"join_changed", "operator_changed", "predicate_or_attribute_changed"}:
            category, rules = "join", ["JOIN"]
        else:
            category, rule = {
                "task_or_engine_changed": ("engine", "ENGINE"), "child_order_changed": ("order", "ORDER"),
                "operator_changed": ("operator", "OPERATOR"), "access_path_changed": ("access_path", "ACCESS_OBJECT"),
                "predicate_or_attribute_changed": ("attribute", "ATTRIBUTE"),
                "node_added": ("structure", "STRUCTURE"), "node_removed": ("structure", "STRUCTURE"),
            }.get(event.type, ("unclassified", "UNCLASSIFIED"))
            rules = [rule]
        key = (category, *pair)
        group = groups.setdefault(key, {"category": category, "pair": pair, "event_refs": [], "rule_refs": {}})
        group["event_refs"].append(number)
        for rule in rules:
            group["rule_refs"].setdefault(rule, []).append(number)

    findings = []
    for group in groups.values():
        category = group["category"]
        fact = {"query_id": events[0].query_id, "category": category, "title": CATEGORIES[category],
                "kind": "auxiliary" if category == "estimate" else "plan",
                "confirmed": compare_status == "changed", "compare_status": compare_status,
                "review_status": "pending_review", "event_refs": group["event_refs"],
                "rules": [{"id": key, "name": RULES[key][0], "meaning": RULES[key][1], "event_refs": refs}
                          for key, refs in sorted(group["rule_refs"].items())]}
        for side, node_id in zip(("old", "new"), group["pair"]):
            nodes, _, branches = indices[side]
            entries = (branches.get(node_id, []) if category == "access_path" else [])
            if not entries and node_id in nodes:
                entries = [(nodes[node_id], 0)]
                if category == "order":
                    entries.extend((child, 1) for child in nodes[node_id].children)
            base_depth = entries[0][1] if entries else 0
            fact[side] = [{"node_id": n.node_id, "depth": depth - base_depth, "operator": n.operator,
                           "access": n.access, "task": n.task, "engine": n.engine, "role": n.role,
                           "attributes": n.attributes, "estimate": n.estimate} for n, depth in entries]
        if category == "access_path":
            relevant_refs = [n for n in group["event_refs"] if events[n - 1].type in
                             {"operator_changed", "access_path_changed", "node_added", "node_removed"}]
            for rule, operators in (("ACCESS_READER", READERS), ("ACCESS_SCAN", SCANS)):
                before = sorted(n["operator"] for n in fact["old"] if n["operator"] in operators)
                after = sorted(n["operator"] for n in fact["new"] if n["operator"] in operators)
                if before != after and not any(r["id"] == rule for r in fact["rules"]):
                    fact["rules"].append({"id": rule, "name": RULES[rule][0], "meaning": RULES[rule][1],
                                          "event_refs": relevant_refs})
            indexes = {}
            for side in ("old", "new"):
                indexes[side] = sorted({match[1] for node in fact[side] if
                    (match := re.search(r"(?:^|,\s*)index:(.*?)(?:,\s*(?:table|partition):|$)", node["access"]))})
            fact["indexes"] = indexes
            if indexes["old"] != indexes["new"]:
                rule = "ACCESS_INDEX"
                fact["rules"].append({"id": rule, "name": RULES[rule][0], "meaning": RULES[rule][1],
                                      "event_refs": relevant_refs})
            fact["rules"].sort(key=lambda r: r["id"])
        fact["finding_id"] = "f_" + digest([fact["query_id"], category, group["pair"]])[:20]
        # Similarity deliberately excludes objects, predicates and cardinalities. It is navigation only.
        signature = [category, [r["id"] for r in fact["rules"]],
                     *[[(n["depth"], n["operator"], n["task"], n["engine"], n["role"]) for n in fact[s]]
                       for s in ("old", "new")]]
        fact["similarity_key"] = digest(signature)
        findings.append(fact)
    return findings


def similar_groups(items):
    groups = {}
    for item in items:
        if item["compare_status"] != "changed":
            continue
        for fact in item.get("findings", []):
            if not fact["confirmed"] or fact["kind"] != "plan":
                continue
            key = fact["similarity_key"]
            group = groups.setdefault(key, {"group_id": "g_" + key[:20], "title": fact["title"],
                                            "members": [], "query_ids": [], "review_status": "pending_review",
                                            "automatic_approval": False})
            group["members"].append({"query_id": item["query_id"], "finding_id": fact["finding_id"]})
            if item["query_id"] not in group["query_ids"]:
                group["query_ids"].append(item["query_id"])
    return [group for group in groups.values() if len(group["query_ids"]) > 1]
