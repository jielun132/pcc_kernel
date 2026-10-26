from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from pcc_tool.models import PlanIR, PlanNode, PlanSnapshot, canonical

OPERATORS = set("""Projection Selection Sort TopN Limit HashAgg StreamAgg TableReader IndexReader
IndexLookUp IndexMerge TableFullScan TableRangeScan IndexFullScan IndexRangeScan TableRowIDScan
Point_Get Batch_Point_Get PointGet BatchPointGet HashJoin MergeJoin IndexJoin IndexHashJoin
IndexMergeJoin Apply Union UnionAll UnionScan TableDual TableSample CTE CTETable CTEFullScan CTEReader CTEStorage
Update Delete Insert Replace Lock Window OrderedWindow MaxOneRow Exists Show Explain ExplainAnalyze
ExchangeSender ExchangeReceiver Shuffle ShuffleReceiver MPPGather Expand PartitionUnion
Sequence ScalarSubQuery PhysicalCTE PhysicalCTETable LimitOffset""".split())

ALIASES = {"id": "id", "operatorid": "id", "estrows": "estimate", "count": "estimate",
           "task": "task", "tasktype": "task", "accessobject": "access",
           "operatorinfo": "attributes", "children": "children", "suboperators": "children"}

ATTRIBUTE_LABELS = {"data", "index", "table", "range", "keep order", "desc", "stats", "handle", "handles",
                    "offset", "count", "rows", "limit", "funcs", "group by", "order by", "partition by",
                    "left key", "right key", "equal", "other cond", "left cond", "right cond", "inner", "outer",
                    "build", "probe", "build side", "probe side", "mpp", "exchange type", "hash col",
                    "cte", "cte id", "seed part", "recursive part", "type", "join type", "partition",
                    "range decided by", "keep order desc", "stream_count", "compression", "input", "output",
                    "outer key", "inner key", "equal cond", "execution info", "data sources"}


def unknown_attributes(text):
    # Inspect only top-level labels; commas/colons in ranges, expressions and quoted literals are data.
    parts, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                if i + 1 < len(text) and text[i+1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    parts.append(text[start:].strip())
    unknown = []
    for part in parts:
        match = re.match(r"^([A-Za-z][A-Za-z_ ]*):", part)
        if match and match[1].lower() not in ATTRIBUTE_LABELS:
            unknown.append(match[1])
    return unknown


def decimal_text(value) -> str | None:
    if value is None or str(value).strip().upper() in {"N/A", "NULL", ""}:
        return None
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("invalid estimate")
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def key_name(name: str) -> str:
    return re.sub(r"[\s_-]", "", name).lower()


def fields(row: dict) -> tuple[dict, dict]:
    known, unknown = {}, {}
    for name, value in row.items():
        key = ALIASES.get(key_name(name))
        if key:
            if key in known:
                raise ValueError("duplicate column")
            known[key] = value
        else:
            unknown[name] = value
    return known, unknown


def text_field(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else canonical(value)


def parse_plan(snapshot: PlanSnapshot) -> PlanIR:
    ir = PlanIR(snapshot.query_id, [])
    if snapshot.capture_status != "ok":
        ir.parse_status = "failed"
        return ir
    all_nodes = []

    def make(row: dict, index: int) -> PlanNode:
        values, unknown = fields(row)
        required = ("id",) if snapshot.format == "tidb_json" else ("id", "estimate", "task", "attributes")
        if not all(k in values for k in required):
            raise ValueError("missing plan columns")
        raw_id = text_field(values["id"])
        clean = raw_id.lstrip(" │├└─\t")
        match = re.fullmatch(r"(.+?)(?:_(\d+))?(?:\((Build|Probe|Seed Part|Recursive Part)\))?", clean)
        if not match:
            raise ValueError("invalid operator id")
        operator, _, role = match.groups()
        task = text_field(values.get("task"))
        engine_match = re.search(r"\[([^]]+)\]", task)
        node = PlanNode(f"n{index}", operator, task, engine_match[1] if engine_match else "tidb" if task == "root" else "",
                        text_field(values.get("access")), text_field(values.get("attributes")),
                        decimal_text(values.get("estimate")), role or "", unknown=unknown,
                        evidence={"row": index, "raw_id": raw_id, "raw": row})
        if operator not in OPERATORS or unknown:
            ir.issues.append({"code": "PLAN_SEMANTICS_UNKNOWN", "node": node.node_id,
                              "operator": operator, "unknown_fields": list(unknown)})
        unfamiliar = unknown_attributes(node.attributes)
        if unfamiliar:
            ir.issues.append({"code": "PLAN_SEMANTICS_UNKNOWN", "node": node.node_id,
                              "unknown_attributes": unfamiliar})
        all_nodes.append(node)
        return node

    try:
        if snapshot.format == "tidb_json":
            if len(snapshot.rows) != 1:
                raise ValueError("invalid json result")
            cell = snapshot.rows[0]
            if isinstance(cell, dict):
                cell = next(iter(cell.values()))
            elif isinstance(cell, (list, tuple)):
                if len(cell) != 1:
                    raise ValueError("invalid json columns")
                cell = cell[0]
            roots = json.loads(cell, parse_float=Decimal) if isinstance(cell, str) else cell
            if not isinstance(roots, list):
                raise ValueError("json roots must be an array")

            def visit(row, depth=0):
                if depth > 256 or len(all_nodes) >= 20000 or not isinstance(row, dict):
                    raise ValueError("plan resource limit")
                node = make(row, len(all_nodes))
                children = fields(row)[0].get("children", []) or []
                if not isinstance(children, list):
                    raise ValueError("invalid children")
                node.children = [visit(child, depth + 1) for child in children]
                return node

            ir.roots = [visit(row) for row in roots]
        elif snapshot.format == "row":
            stack = []
            for index, row in enumerate(snapshot.rows):
                if index >= 20000:
                    raise ValueError("plan resource limit")
                if not isinstance(row, dict):
                    if len(row) != len(snapshot.columns):
                        raise ValueError("row column mismatch")
                    row = dict(zip(snapshot.columns, row))
                node = make(row, index)
                raw_id = text_field(fields(row)[0]["id"])
                prefix = raw_id[:len(raw_id) - len(raw_id.lstrip(" │├└─\t"))]
                if "\t" in prefix:
                    raise ValueError("unverified indentation")
                if len(prefix) % 2:
                    raise ValueError("invalid indentation")
                depth = len(prefix) // 2
                if depth > len(stack) or depth > 256:
                    raise ValueError("missing parent")
                stack = stack[:depth]
                if stack:
                    stack[-1].children.append(node)
                else:
                    ir.roots.append(node)
                stack.append(node)
        else:
            raise ValueError("unknown format")
        if not ir.roots:
            raise ValueError("empty plan")
    except (ValueError, TypeError, KeyError, InvalidOperation, RecursionError, StopIteration):
        ir.parse_status = "failed"
        ir.issues.append({"code": "PLAN_PARSE_FAILED", "message": "计划格式、列、层级或数值不符合已支持契约"})
        return ir
    # Only known operator IDs are normalized. Literals, Column# IDs, ranges and index names stay intact.
    ids, anchors, paths = {}, {}, {}
    def locate(nodes, prefix=()):
        for index, node in enumerate(nodes):
            paths[node.node_id] = prefix + (index,)
            locate(node.children, prefix + (index,))
    locate(ir.roots)
    def accessed_tables(node):
        own = re.search(r"(?:^|,\s*)table:([^,]+)", node.access)
        return [own[1]] if own else sorted(t for child in node.children for t in accessed_tables(child))
    for node in all_nodes:
        raw = node.evidence["raw_id"].lstrip(" │├└─")
        raw = re.sub(r"\((?:Build|Probe|Seed Part|Recursive Part)\)$", "", raw)
        anchor = f"{node.operator}@{','.join(accessed_tables(node))}|{node.task}|{node.access}|{node.role}"
        if raw in ids:
            ir.issues.append({"code": "PLAN_SEMANTICS_UNKNOWN", "node": node.node_id,
                              "message": "原始计划算子 ID 重复，引用目标不唯一"})
        ids[raw] = anchor
        anchors[anchor] = anchors.get(anchor, 0) + 1
    # Repeated UNION branches and nested aggregates are valid, distinct nodes. Their
    # exact tree positions identify references without retaining unstable numeric IDs.
    base_ids = ids.copy()
    for node in all_nodes:
        raw = node.evidence["raw_id"].lstrip(" │├└─")
        raw = re.sub(r"\((?:Build|Probe|Seed Part|Recursive Part)\)$", "", raw)
        if anchors[base_ids[raw]] > 1:
            ids[raw] = base_ids[raw] + "|path:" + ".".join(map(str, paths[node.node_id]))
    pattern = re.compile(r"('(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|`(?:``|[^`])*`)|(\b[A-Za-z][A-Za-z_]*_\d+\b)")
    for node in all_nodes:
        node.attributes = pattern.sub(lambda m: m[0] if m[1] else ids.get(m[0], m[0]), node.attributes)
    if ir.issues:
        ir.parse_status = "partial"
    return ir
