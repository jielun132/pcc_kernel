"""Explicit common-workload heuristic for multi-operator plan tradeoffs.

This is PCC's versioned resource model, NOT TiDB estCost and NOT latency. Both
plans use the same DDL width bounds, units and weights. Unknown operators,
engines, widths, statistics or row-count conventions fail closed. A candidate
must contain a supported physical tradeoff; absence of another match is not
itself a candidate. The component vector is archived for inspection/replay.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import sqlglot
from sqlglot import exp

from pcc_tool.plan.assessment import COLUMN, limits, number, split_list, walk

WEIGHTS = {"scan_bytes": 1, "lookup_bytes": 4, "network_bytes": 0.5, "cpu_units": 8, "memory_bytes": 0.1}
LABELS = {"scan_bytes": "扫描字节估算", "lookup_bytes": "回表字节估算", "network_bytes": "传输字节估算",
          "cpu_units": "CPU 工作量", "memory_bytes": "工作内存字节估算"}
ALLOWED = {"TableFullScan", "TableRangeScan", "IndexFullScan", "IndexRangeScan", "TableRowIDScan", "Point_Get",
           "TableReader", "IndexReader", "IndexLookUp", "IndexMerge", "Projection", "Selection", "Limit", "TopN", "Sort",
           "HashJoin", "IndexJoin", "IndexHashJoin", "IndexMergeJoin", "MergeJoin", "HashAgg", "StreamAgg",
           "Shuffle", "ShuffleReceiver", "Window", "Union", "TableDual", "Update", "Delete"}
SCANS = {"TableFullScan", "TableRangeScan", "IndexFullScan", "IndexRangeScan", "TableRowIDScan", "Point_Get"}
JOINS = {"HashJoin", "IndexJoin", "IndexHashJoin", "IndexMergeJoin", "MergeJoin"}
EXPRESSIONS = {"eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "isnull", "nulleq", "cast", "decimal", "var_string",
               "plus", "minus", "mul", "div", "count", "sum", "avg", "min", "max", "firstrow", "like",
               "row_number", "rank", "dense_rank", "over"}


def type_width(data_type):
    """Conservative bounded SQL value width, never an invented TEXT average."""
    if re.fullmatch(r"(?:BIGINT|INT|TINYINT|SMALLINT|MEDIUMINT)(?:\(\d+\))?(?: UNSIGNED)?", data_type):
        return 8
    if re.fullmatch(r"(?:FLOAT|DOUBLE|DATE|DATETIME|TIMESTAMP|TIME|YEAR)(?:\(\d+\))?", data_type):
        return 16
    size = re.fullmatch(r"(?:VAR)?(?:CHAR|BINARY)\((\d+)\)", data_type)
    if size:
        return int(size[1]) * (1 if "BINARY" in data_type else 4) + 2
    size = re.fullmatch(r"DECIMAL\((\d+),\s*\d+\)", data_type)
    return (int(size[1]) + 1) // 2 + 2 if size else None


def schema_tables(query, metadata):
    try:
        tree = sqlglot.parse_one(query.sql_text or "", read="mysql")
    except (ValueError, sqlglot.errors.ParseError):
        return None
    if not isinstance(tree, (exp.Query, exp.Update, exp.Delete)):
        return None
    result = {}
    delete_targets = {id(t) for t in tree.args.get("tables", [])} if isinstance(tree, exp.Delete) else set()
    for table in tree.find_all(exp.Table):
        if id(table) in delete_targets:
            continue
        info = metadata.get((table.db or query.schema) + "." + table.name)
        if not info or not info.get("stats_ready"):
            return None
        for alias in (table.name, table.alias_or_name):
            if alias in result and result[alias] != info:
                return None
            result[alias] = info
    return result or None


def tradeoff(old, new):
    def shape(node):
        return node.operator, node.access, node.role, tuple(shape(c) for c in node.children)
    a, b = [n for r in old.roots for n in walk(r)], [n for r in new.roots for n in walk(r)]
    leaves = lambda nodes: [(n.operator, n.access) for n in nodes if n.operator in SCANS]
    if leaves(a) != leaves(b):
        return True
    joins = lambda nodes: [shape(n) for n in nodes if n.operator in JOINS]
    if joins(a) != joins(b):
        return True
    counts_a, counts_b = Counter(n.operator for n in a), Counter(n.operator for n in b)
    if counts_a["Window"] and counts_b["Window"] and (counts_a["Shuffle"], counts_a["Sort"]) != (counts_b["Shuffle"], counts_b["Sort"]):
        return True
    if counts_a["IndexMerge"] and counts_b["IndexMerge"] and counts_a["TopN"] != counts_b["TopN"]:
        return True
    return bool(counts_a["Limit"] and counts_b["Limit"] and counts_a["Limit"] != counts_b["Limit"])


def workload(plan, tables):
    units = dict.fromkeys(WEIGHTS, 0.0)
    assumptions = set()

    def visit(n):
        if n.operator not in ALLOWED or n.unknown or n.task not in {"root", "cop[tikv]"} or n.engine not in {"tidb", "tikv"}:
            raise ValueError("unsupported operator or engine")
        if "stats:pseudo" in n.attributes or "stats:partial" in n.attributes:
            raise ValueError("unreliable statistics")
        if n.operator in {"Projection", "Selection", "HashAgg", "StreamAgg", "Window"}:
            functions = {name.lower() for name in re.findall(r"\b([A-Za-z_][\w]*)\(", n.attributes)}
            if not functions <= EXPRESSIONS or "distinct" in n.attributes.lower():
                raise ValueError("unsupported expression work")
        unary = {"TableReader", "IndexReader", "Projection", "Selection", "Limit", "TopN", "Sort", "HashAgg", "StreamAgg",
                 "Shuffle", "ShuffleReceiver", "Window", "Update", "Delete"}
        if (n.operator in SCANS | {"TableDual"} and n.children or n.operator in unary and len(n.children) != 1
                or n.operator in JOINS | {"IndexLookUp"} and len(n.children) != 2
                or n.operator in {"IndexMerge", "Union"} and len(n.children) < 2):
            raise ValueError("unsupported arity")
        count = number(n.estimate)
        if count is None and n.operator not in {"Update", "Delete"}:
            raise ValueError("missing estimate")
        # A shared floor prevents a display-only zero -> one change from
        # introducing a zero denominator or pretending an empty physical scan.
        rows = max(1.0, float(count or 0))
        if not math.isfinite(rows) or rows > 1e12:
            raise ValueError("unbounded estimate")
        children = [visit(c) for c in n.children]
        width = sum(w for _, w in children) if n.operator in JOINS else (children[0][1] if children else 0)
        input_rows = sum(r for r, _ in children)
        if n.operator in SCANS:
            table = re.fullmatch(r"table:([^,]+)(?:, index:[^(]+\(([^)]+)\))?", n.access)
            info = tables.get(table[1]) if table else None
            if not info:
                raise ValueError("unknown table")
            if n.operator.startswith("Index"):
                if not table[2]:
                    raise ValueError("unknown index width")
                columns = list(dict.fromkeys([c.strip() for c in table[2].split(",")] + info.get("primary_key", [])))
            else:
                columns = list(info["columns"])
            sizes = [type_width(info["columns"].get(c, "")) for c in columns]
            if not sizes or any(w is None for w in sizes):
                raise ValueError("unbounded column width")
            width = 8 + sum(sizes) + len(sizes)  # row/handle + nullable slots
            units["lookup_bytes" if n.operator in {"TableRowIDScan", "Point_Get"} else "scan_bytes"] += rows * width
        elif n.operator in {"TableReader", "IndexReader", "IndexLookUp", "IndexMerge"}:
            # Both the handle phase and row phase of a double read transfer
            # data; child estRows is TOTAL inner-side rows since TiDB 6.4.
            units["network_bytes"] += sum(r * w for r, w in children)
            if n.operator == "IndexLookUp" and len(children) == 2:
                width = children[-1][1]
                units["cpu_units"] += input_rows
            if n.operator == "IndexMerge":
                width = children[-1][1]
                units["cpu_units"] += input_rows * math.log2(max(2, len(children)))
        elif n.operator == "Projection":
            parts = split_list(n.attributes)
            if not parts:
                raise ValueError("unknown projection")
            # Known expression width bounds are kept; derived values conservatively
            # retain their input width. This affects transfer only if pushed.
            projected = []
            for part in parts:
                column = re.fullmatch(r"(" + COLUMN + r")(?:->Column#\d+)?", part)
                name = column[1].split(".") if column else []
                if len(name) == 3:
                    info = tables.get(name[-2], {})
                    size = type_width(info.get("columns", {}).get(name[-1], ""))
                else:
                    size = None
                projected.append(size)
            if all(w is not None for w in projected):
                width = 8 + sum(projected) + len(projected)
            units["cpu_units"] += input_rows * len(parts) * 0.1
        elif n.operator == "Selection":
            units["cpu_units"] += input_rows * max(1, len(re.findall(r"[a-z_]+\(", n.attributes)))
        elif n.operator in {"Sort", "TopN"}:
            bound = limits(n) if n.operator == "TopN" else None
            if n.operator == "TopN" and not bound:
                raise ValueError("unknown TopN bound")
            k = min(input_rows, bound[1] + bound[2]) if bound else input_rows
            units["cpu_units"] += input_rows * math.log2(max(2, k))
            units["memory_bytes"] += k * width
        elif n.operator == "Limit":
            if not limits(n):
                raise ValueError("unknown limit")
            units["cpu_units"] += min(rows, input_rows) * 0.05
        elif n.operator in JOINS:
            if len(children) != 2 or {c.role for c in n.children} != {"Build", "Probe"}:
                raise ValueError("unknown join roles")
            if "CARTESIAN" in n.attributes:
                raise ValueError("cartesian selectivity needs proof")
            units["cpu_units"] += input_rows * (1 if n.operator == "MergeJoin" else 2)
            if n.operator == "HashJoin":
                units["memory_bytes"] += sum(r * w for c, (r, w) in zip(n.children, children) if c.role == "Build")
            elif n.operator.startswith("Index"):
                outer_rows = next(r for c, (r, _) in zip(n.children, children) if c.role == "Build")
                units["cpu_units"] += outer_rows * 5
                if n.operator == "IndexMergeJoin":
                    units["cpu_units"] += outer_rows * math.log2(max(2, outer_rows))
                elif n.operator == "IndexHashJoin":
                    units["memory_bytes"] += sum(r * w for c, (r, w) in zip(n.children, children) if c.role == "Build")
                assumptions.add("Index Join 内侧 estRows 按 v6.4 起的总量口径使用，不再乘外侧行数；每个外侧键计 5 个探测工作单位。")
        elif n.operator in {"HashAgg", "StreamAgg"}:
            units["cpu_units"] += input_rows * max(1, n.attributes.count("funcs:")) * (2 if n.operator == "HashAgg" else 1)
            units["memory_bytes"] += rows * width if n.operator == "HashAgg" else width
        elif n.operator == "Window":
            units["cpu_units"] += input_rows * max(1, n.attributes.count("->"))
        elif n.operator == "Shuffle":
            concurrency = re.search(r"concurrency:(\d+)", n.attributes)
            if not concurrency:
                raise ValueError("unknown shuffle parallelism")
            units["cpu_units"] += input_rows + int(concurrency[1]) * 16
            assumptions.add("模型比较总工作量，不把并发数直接折算为耗时加速。")
        elif n.operator == "Union":
            units["cpu_units"] += input_rows * 0.1
        elif n.operator == "TableDual":
            if n.attributes not in {"rows:0", "rows:1"}:
                raise ValueError("unknown constant source")
            rows, width = float(n.attributes[-1]), 8
        return rows, width

    try:
        for root in plan.roots:
            visit(root)
    except (ValueError, OverflowError, StopIteration):
        return None
    return dict(units={key: round(value, 4) for key, value in units.items()}, assumptions=sorted(assumptions))


def estimate_tradeoff(query, old, new, metadata, captures):
    if not old or not new or old.parse_status != "ok" or new.parse_status != "ok":
        return None
    versions = {getattr(captures.get(side), "version", "") for side in ("old", "new")}
    if not versions <= {"v6.5.12", "v8.5.5"} or not tradeoff(old, new):
        return None
    # A parameterized scan can change displayed selectivity conventions while
    # its enclosing lookup stays the same. Do not score such a change as I/O.
    def dynamic(plan):
        return {(n.operator, n.access, n.attributes): number(n.estimate) for r in plan.roots for n in walk(r)
                if n.operator in SCANS and "range: decided by" in n.attributes}
    da, db = dynamic(old), dynamic(new)
    for key in da.keys() & db.keys():
        a, b = da[key], db[key]
        if a is None or b is None or max(a, b) > max(1, min(a, b)) * number("1.2"):
            return None
    tables = schema_tables(query, metadata)
    if not tables:
        return None
    workloads = [workload(plan, tables) for plan in (old, new)]
    if any(value is None for value in workloads):
        return None
    scores = [sum(w["units"][key] * value for key, value in WEIGHTS.items()) for w in workloads]
    if not all(math.isfinite(s) and s > 0 for s in scores):
        return None
    serialized_scores = [f"{score:.4f}" for score in scores]
    a, b = [number(score) for score in serialized_scores]
    ratio = float(b / a)
    # Symmetric tolerance: reversing the inputs preserves equivalence and
    # exchanges better/worse. This is a model policy, not a timing guarantee.
    direction = "better" if b * 6 < a * 5 else "worse" if b * 5 > a * 6 else "equivalent"
    for value in workloads:
        value["units"] = {key: f"{amount:.4f}" for key, amount in value["units"].items()}
    return dict(rule_id="A22", direction=direction, guards=[], basis="estimated_work",
        old_nodes=[r.node_id for r in old.roots], new_nodes=[r.node_id for r in new.roots],
        reason=f"PCC 工作量模型 v1：旧计划 {scores[0]:.2f} 分，新计划 {scores[1]:.2f} 分（新/旧 {ratio:.3f}）；同一模型按 max/min ≤ 1.20 视为基本等价。依据扫描、回表、传输、CPU 和内存估算综合判断。分数不是 TiDB estCost，也不是实测耗时；数据倾斜、缓存及并发可能改变实际方向。",
        model=dict(version="1", tolerance_ratio="1.2", weights={k: str(v) for k, v in WEIGHTS.items()}, labels=LABELS, old=workloads[0], new=workloads[1],
                   old_score=serialized_scores[0], new_score=serialized_scores[1],
                   assumptions=["列宽使用 DDL 有界最大宽度；无界类型不自动评分。", "模型衡量总工作量，不预测墙钟时间、溢出或数据倾斜。", "回表权重 4、传输权重 0.5、CPU 权重 8、内存权重 0.1 是 PCC 策略，并非 TiDB 官方常数。", "两端子节点 estRows 使用同一总量口径；小于一行按一行计。"] ))
