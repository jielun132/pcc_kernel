"""Conservative performance hypotheses over confirmed plan differences.

Detection is independent of card selection. Browser and SDK evaluate the same
archived evidence; absence of a match is never evidence of equivalence.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from pcc_tool.models import primitive
from pcc_tool.plan.empty_replay import replay_reviews, review_reason, view_evidence
from pcc_tool.plan.rule_catalog import ASSESSMENT_VERSION, DEFAULT_RULES, DEFAULT_THRESHOLDS, REQUIRED_DATA_RULES, RULE_CATALOG

READERS = {"TableReader", "IndexReader"}
COLUMN = r"(?:Column#\d+|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){1,2})"


def split_list(text):
    """Split EXPLAIN expression lists without splitting strings or functions."""
    parts, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "'\"`":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
        i += 1
    return [] if quote or depth else parts + [text[start:].strip()]


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def number(value):
    try:
        if value is None or not re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", str(value)):
            return None
        n = Decimal(value)
        return Fraction(n) if n.is_finite() and abs(n.as_tuple().exponent) <= 1000 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def attrs(node):
    # Only a pure reader reference is redundant with its one child. Extra
    # operator_info fields, partition modes, etc. remain part of the signature.
    raw = node.evidence.get("raw", {})
    raw = raw.get("operator info", raw.get("operatorInfo", "")) if isinstance(raw, dict) else ""
    if node.operator in READERS and len(node.children) == 1 and re.fullmatch(r"(?:data|index):[A-Za-z_]+_\d+", raw):
        return "child-reference"
    return node.attributes


def signature(node, *, ignore_estimate=False, ignore_role=False):
    return (node.operator, node.task, node.engine, node.access, attrs(node),
            None if ignore_estimate else number(node.estimate), "" if ignore_role else node.role,
            node.unknown, [signature(c, ignore_estimate=ignore_estimate, ignore_role=ignore_role) for c in node.children])


def same(a, b, **kwargs):
    return signature(a, **kwargs) == signature(b, **kwargs)


def shell(a, b, *, location=False, estimate=False, role=False):
    return (a.operator == b.operator and a.access == b.access and attrs(a) == attrs(b)
            and (role or a.role == b.role) and a.unknown == b.unknown
            and (location or (a.task, a.engine) == (b.task, b.engine))
            and (estimate or number(a.estimate) == number(b.estimate)))


def alias_only(a, b):
    if a.operator != "Projection" or b.operator != "Projection":
        return False
    before, after = split_list(a.attributes), split_list(b.attributes)
    if not before or len(before) != len(after):
        return False
    def source(text):
        match = re.fullmatch(f"({COLUMN})(?:->Column#\\d+)?", text)
        return match[1] if match else None
    return all(x == y or source(x) is not None and source(x) == source(y) for x, y in zip(before, after))


def simple_predicates(text):
    """A deliberately tiny safe equivalence language, without SQL coercion."""
    parts = split_list(text)
    if len(parts) == 1 and text.startswith("and(") and text.endswith(")"):
        parts = split_list(text[4:-1])
    normalized = []
    for part in parts:
        match = re.fullmatch(r"(eq|ne|lt|le|gt|ge)\((-?\d+),\s*(-?\d+)\)", part)
        if not match:
            return None
        op, left, right = match.groups()
        a, b = int(left), int(right)
        if a > b:
            a, b = b, a
            op = {"eq": "eq", "ne": "ne", "lt": "gt", "gt": "lt", "le": "ge", "ge": "le"}[op]
        normalized.append((op, a, b))
    return sorted(normalized) if normalized else None


def limits(node):
    match = re.fullmatch(r"(?:(.*), )?offset:(\d+), count:(\d+)", node.attributes)
    return (match[1] or "", int(match[2]), int(match[3])) if match else None


def safe_filter(text, metadata):
    """Only deterministic comparisons over proven integer columns may move."""
    if not metadata:
        return False
    parts = split_list(text)
    if len(parts) > 1:
        return all(safe_filter(part, metadata) for part in parts)
    if re.match(r"(?:and|or)\(", text) and text.endswith(")"):
        args = split_list(text[text.index("(") + 1:-1])
        return len(args) >= 2 and all(safe_filter(arg, metadata) for arg in args)
    match = re.fullmatch(r"(?:eq|ne|lt|le|gt|ge)\((.+)\)", text)
    if not match:
        return False
    args = split_list(match[1])
    def operand(value):
        return bool(re.fullmatch(r"-?\d+", value) or re.fullmatch(COLUMN, value) and re.match(
            r"(?:BIGINT|INT|SMALLINT|TINYINT|MEDIUMINT)\b", metadata["columns"].get(value.split(".")[-1], "")))
    return len(args) == 2 and all(operand(arg) for arg in args)


def root_cop(root, cop):
    return root.task == "root" and root.engine == "tidb" and cop.task == "cop[tikv]" and cop.engine == "tikv"


def input_guard(input_rows, output_rows, *, reduction=False, minimum=None):
    """Archive numeric operands, not a decision tied to today's UI settings."""
    if number(input_rows) is None or number(output_rows) is None:
        return [{"kind": "missing", "label": "缺少可用估算行数"}]
    guards = [{"kind": "minimum", "value": str(input_rows), "setting": "min_rows", "label": "输入行数"},
              {"kind": "reduction" if reduction else "ratio", "input": str(input_rows), "output": str(output_rows),
               "setting": "reduction_pct" if reduction else "max_ratio_pct", "label": "减少比例" if reduction else "K / 输入比例"}]
    if minimum:
        guards.append({"kind": "minimum", "value": str(input_rows), "constant": str(minimum), "label": "规则最低输入行数"})
    return guards


def guard_pass(guard, thresholds):
    setting = number(guard.get("constant", thresholds.get(guard.get("setting"))))
    if setting is None:
        return False
    if guard["kind"] == "minimum":
        value = number(guard.get("value"))
        return value is not None and value >= setting
    a, b = number(guard.get("input")), number(guard.get("output"))
    if a is None or b is None or a <= 0:
        return False
    if guard["kind"] == "ratio":
        return b * 100 <= a * setting
    if guard["kind"] == "reduction":
        return b < a and (a - b) * 100 >= a * setting
    if guard["kind"] == "estimate":
        return b > 0 and max(a, b) * 100 <= min(a, b) * (100 + setting)
    return False


def evaluate(evidence, selected=None, thresholds=None):
    selected = set(DEFAULT_RULES if selected is None else selected)
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reviews = [*evidence.get("mandatory_reviews", []), *(r for r in evidence.get("runtime_notes", []) if r["rule_id"] in selected)]
    mandatory = [r["rule_id"] + " · " + r["reason"] for r in reviews]
    if evidence["scope"] != "changed":
        if mandatory:
            return dict(status="review", matched=[], uncovered=[], reasons=mandatory + list(evidence["blockers"]))
        return dict(status="excluded", matched=[], uncovered=[], reasons=["只对已确认存在差异的 SQL 预判"])
    matched, rejected = [], []
    for match in evidence["matches"]:
        if match["rule_id"] not in selected or not set(match.get("requires_rules", [])).issubset(selected):
            continue
        failures = [g["label"] for g in match.get("guards", []) if not guard_pass(g, thresholds)]
        if failures:
            rejected.append((match, match["rule_id"] + "：" + "、".join(failures) + "未满足阈值"))
        else:
            matched.append(match)
    covered = {str(ref) for m in matched for ref in m["event_refs"]}
    uncovered = [ref for ref in evidence["event_refs"] if str(ref) not in covered]
    directions = {m["direction"] for m in matched} - {"equivalent"}
    reasons = mandatory + list(evidence["blockers"]) + [reason for match, reason in rejected
        if any(str(ref) not in covered for ref in match["event_refs"])]
    if uncovered:
        reasons.append("存在未被所选规则完整覆盖的差异")
    if len(directions) > 1:
        reasons.append("同时命中变优和变差，不抵消")
    status = "review" if reasons or not matched else next(iter(directions)) if directions else "equivalent"
    return dict(status=status, matched=[m["id"] for m in matched], uncovered=uncovered, reasons=reasons)


def schema_evidence(bundle):
    """Minimal, non-secret DDL evidence, also archived for offline replay."""
    import sqlglot
    from sqlglot import exp
    from pcc_tool.artifacts.store import safe_path
    result = {}
    views = []
    if not bundle.path:
        return result
    for obj in bundle.manifest.get("objects", []):
        try:
            if obj.get("kind") == "view":
                result[obj["schema"] + "." + obj["table"]] = dict(view_reviews=[["D10", "缺少可读的视图定义。"]],
                    view_dependencies=obj.get("dependencies", []))
            text = safe_path(Path(bundle.path), obj["ddl_file"]).read_text()
            if obj.get("kind") == "view":
                views.append((obj, text))
                continue
            tree = sqlglot.parse_one(text, read="mysql")
            if not isinstance(tree, exp.Create) or not isinstance(tree.this, exp.Schema):
                continue
            columns = {c.name: c.args["kind"].sql(dialect="mysql") for c in tree.this.expressions if isinstance(c, exp.ColumnDef)}
            pk = [key.name for constraint in tree.this.expressions if isinstance(constraint, exp.PrimaryKey) for key in constraint.expressions]
            for col in tree.this.expressions:
                if isinstance(col, exp.ColumnDef) and any(isinstance(c.args.get("kind"), exp.PrimaryKeyColumnConstraint) for c in col.constraints):
                    pk.append(col.name)
            result[obj["schema"] + "." + obj["table"]] = dict(columns=columns, primary_key=pk,
                clustered_pk="NONCLUSTERED" not in text and "CLUSTERED" in text, stats_ready=obj.get("stats_ready") is True,
                ddl_hash=obj.get("ddl_hash"))
        except (KeyError, ValueError, OSError, sqlglot.errors.ParseError):
            continue
    for obj, text in views:
        result[obj["schema"] + "." + obj["table"]] = dict(
            view_reviews=view_evidence(text, obj["schema"], result), view_dependencies=obj.get("dependencies", []))
    return result


def query_table(query, metadata):
    """Only a plain single-table SELECT supplies uniqueness/column evidence."""
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(query.sql_text or "", read="mysql")
        tables = list(tree.find_all(exp.Table))
        if not isinstance(tree, exp.Select) or len(tables) != 1 or tree.args.get("joins") or tree.args.get("with_"):
            return None, None
        table = tables[0]
        entry = metadata.get((table.db or query.schema) + "." + table.name)
        return tree, entry if entry and "columns" in entry else None
    except (ValueError, sqlglot.errors.ParseError):
        return None, None


def false_query(query):
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(query.sql_text or "", read="mysql")
        if not isinstance(tree, exp.Select) or any(tree.args.get(k) for k in ("group", "having", "with_", "joins")):
            return False
        if any(not isinstance(e, (exp.Column, exp.Star, exp.Literal)) for e in tree.expressions):
            return False
        where = tree.args.get("where")
        value = where.this if where else None
        if isinstance(value, exp.Boolean):
            return value.this is False
        if isinstance(value, exp.Literal) and not value.is_string:
            return value.this == "0"
        return (isinstance(value, exp.EQ) and all(isinstance(e, exp.Literal) and not e.is_string for e in (value.this, value.expression))
                and Decimal(value.this.this) != Decimal(value.expression.this))
    except (ValueError, InvalidOperation, sqlglot.errors.ParseError):
        return False


def detect(query, old, new, events, status, issues=(), metadata=None, captures=None):
    evidence = dict(version=ASSESSMENT_VERSION, scope=status, event_refs=[str(i) for i in range(1, len(events) + 1)],
                    matches=[], hints=[], blockers=[], cautions=[], mandatory_reviews=[], runtime_notes=[])
    if status == "not_run":
        return evidence
    nodes = {side: [n for root in ir.roots for n in walk(root)] if ir else []
             for side, ir in (("old", old), ("new", new))}
    for review in replay_reviews(query, nodes, metadata or {}):
        field = "mandatory_reviews" if review["rule_id"] in REQUIRED_DATA_RULES else "runtime_notes"
        evidence[field].append(review)
    if status != "changed" or not old or not new:
        return evidence
    if old.parse_status != "ok" or new.parse_status != "ok" or any(e.requires_review for e in events):
        evidence["blockers"].append("计划解析或节点对应不完整")
    if any(i.get("code") == "CONTEXT_DIFFERENCE" for i in issues):
        evidence["cautions"].append("两端存在非阻断的上下文差异；本结论只评估所见计划，不能直接归因于版本。")
    old_nodes = {n.node_id: n for n in nodes["old"]}
    new_nodes = {n.node_id: n for n in nodes["new"]}
    tree, table_meta = query_table(query, metadata or {})
    known_stats = bool(table_meta and table_meta["stats_ready"])
    no_pseudo = all("stats:pseudo" not in n.attributes and "stats:partial" not in n.attributes for n in [*old_nodes.values(), *new_nodes.values()])
    def cast_types(nodes):
        result = {}
        for node in nodes:
            for column, data_type in re.findall(r"cast\((" + COLUMN + r"), (var_string\(\d+\)(?: BINARY)?)\)", node.attributes):
                result.setdefault(column, set()).add(data_type)
        return result
    old_casts, new_casts = cast_types(old_nodes.values()), cast_types(new_nodes.values())
    for column in sorted(old_casts.keys() & new_casts.keys()):
        if old_casts[column] != new_casts[column]:
            evidence["blockers"].append("CAST 返回类型长度改变，尚不能证明截断/比较语义相同：" + column + "："
                + "/".join(sorted(old_casts[column])) + " → " + "/".join(sorted(new_casts[column])))

    def add(rule, direction, refs, before, after, reason, guards=()):
        if not refs:
            return
        evidence["matches"].append(dict(id=f"m{len(evidence['matches']) + 1}", rule_id=rule, direction=direction,
            event_refs=[str(r) for r in refs], old_nodes=[n.node_id for n in before], new_nodes=[n.node_id for n in after],
            reason=reason, guards=list(guards)))

    # Neutral field evidence is narrow; it cannot swallow unrelated node changes.
    physical_same = (len(old.roots) == len(new.roots) and all(same(a, b, ignore_estimate=True) for a, b in zip(old.roots, new.roots)))
    for i, event in enumerate(events, 1):
        a, b = old_nodes.get(event.old_node), new_nodes.get(event.new_node)
        if not a or not b:
            continue
        if event.type == "predicate_or_attribute_changed" and alias_only(a, b):
            add("E01", "equivalent", [i], [a], [b], "同一投影位置、同一来源列，仅 EXPLAIN 别名标记变化。")
        if event.type == "predicate_or_attribute_changed" and a.operator == b.operator == "Selection":
            predicates = simple_predicates(a.attributes)
            if predicates is not None and predicates == simple_predicates(b.attributes):
                add("E03", "equivalent", [i], [a], [b], "白名单中的纯整数比较谓词等价。")
        if event.type == "estimate_changed" and physical_same:
            add("E05", "equivalent", [i], [a], [b], "物理计划相同；仅按所选估算值容差预判。", [dict(kind="estimate", input=a.estimate,
                output=b.estimate, setting="estimate_pct", label="估算值容差")])

    def transformation(rule, a, b, direction, reason, guards=()):
        left, right = list(walk(a)), list(walk(b))
        ids_a, ids_b = {n.node_id for n in left}, {n.node_id for n in right}
        # Reject correspondences crossing the proven subtree boundary.
        refs = []
        for i, event in enumerate(events, 1):
            touches = event.old_node in ids_a or event.new_node in ids_b
            inside = (event.old_node is None or event.old_node in ids_a) and (event.new_node is None or event.new_node in ids_b)
            if touches and not inside:
                return
            if touches:
                refs.append(i)
        add(rule, direction, refs, left, right, reason, guards)

    def patterns(a, b):
        # Patterns are stated from more work to less work, then tried both ways.
        if (a.operator == b.operator == "Projection" and len(a.children) == 1 and shell(a, b)
                and a.children[0].operator == "Projection" and same(a.children[0], b)
                and all(re.fullmatch(COLUMN, part) for part in split_list(a.attributes)) and a.attributes):
            return "E02", "相邻透传 Projection 的完整列列表、位置、任务和输出相同；只移除重复的一层。", []
        if a.operator == "Selection" and len(a.children) == 1 and b.operator in READERS and len(b.children) == 1:
            reader, pushed = a.children[0], b.children[0]
            if (reader.operator == b.operator and len(reader.children) == len(pushed.children) == 1 and pushed.operator == "Selection"
                    and shell(reader, b, estimate=True) and shell(a, pushed, location=True) and root_cop(a, pushed)
                    and same(reader.children[0], pushed.children[0]) and number(b.estimate) == number(a.estimate)
                    and known_stats and no_pseudo and safe_filter(a.attributes, table_meta)):
                return "A06", "同一过滤移至 TiKV，扫描不变，传输候选减少。", input_guard(reader.estimate, b.estimate, reduction=True)
        if a.operator in {"TopN", "Limit"} and shell(a, b) and len(a.children) == len(b.children) == 1:
            ar, br = a.children[0], b.children[0]
            if ar.operator in READERS and ar.operator == br.operator and len(ar.children) == len(br.children) == 1:
                pushed = br.children[0]
                la, lp = limits(a), limits(pushed)
                if (pushed.operator == a.operator and len(pushed.children) == 1 and la and lp and la[0] == lp[0]
                        and lp[1:] == (0, la[1] + la[2]) and la[2] > 0 and root_cop(a, pushed)
                        and not pushed.access and not pushed.role
                        and shell(ar, br, estimate=True) and same(ar.children[0], pushed.children[0]) and known_stats and no_pseudo):
                    return ("A08" if a.operator == "TopN" else "A09"), "保留最终截断，扫描端增加局部截断；其余扫描完全相同。", input_guard(ar.estimate, str(la[1] + la[2]))
        if a.operator == "Limit" and b.operator == "TopN" and len(a.children) == len(b.children) == 1:
            sort = a.children[0]
            la, lb = limits(a), limits(b)
            if (sort.operator == "Sort" and len(sort.children) == 1 and la and lb and not la[0] and lb[0] == sort.attributes
                    and la[1:] == lb[1:] and la[2] > 0 and a.task == b.task == sort.task == "root"
                    and a.engine == b.engine == sort.engine == "tidb" and not a.access and not b.access and not sort.access
                    and a.role == b.role and same(sort.children[0], b.children[0]) and number(a.estimate) == number(b.estimate)
                    and known_stats and no_pseudo):
                return "A11", "同排序和截断参数，有界 TopN 替代全量 Sort + Limit。", input_guard(sort.children[0].estimate, str(la[1] + la[2]))
        if (b.operator == "TableDual" and b.attributes == "rows:0" and number(b.estimate) == 0 and not b.children
                and a.operator == "Selection" and a.attributes in {"0", "false", "eq(1, 0)", "eq(0, 1)"}
                and number(a.estimate) == 0 and false_query(query) and a.task == b.task == "root"):
            return "A17", "SQL 常量条件可证明结果为空；移除了仍被保留的扫描分支。", []
        if table_meta:
            result = access_patterns(a, b, tree, table_meta, known_stats and no_pseudo)
            if result:
                return result
        if known_stats and no_pseudo:
            result = aggregate_pattern(a, b)
            if result:
                return result
        return None

    def visit(a, b):
        if same(a, b):
            return
        for before, after, direction in ((a, b, "better"), (b, a, "worse")):
            found = patterns(before, after)
            if found:
                rule, reason, guards = found
                conclusion = "equivalent" if rule.startswith("E") else direction
                transformation(rule, a, b, conclusion, reason if direction == "better" else "反向变化：" + reason, guards)
                return
        # Ordered structural pairing only. Join reordering or replacement does
        # not authorize searching another branch for a convenient rule match.
        if a.operator == b.operator and a.role == b.role and len(a.children) == len(b.children):
            for left, right in zip(a.children, b.children):
                visit(left, right)

    if len(old.roots) == len(new.roots):
        for a, b in zip(old.roots, new.roots):
            visit(a, b)
    from pcc_tool.plan.assessment_proof import prove_plans
    proof = prove_plans(query, old, new, metadata or {}, captures or {})
    if proof:
        required = sorted({step["rule_id"] for step in proof})
        for step in proof:
            evidence["matches"].append(dict(step, id=f"m{len(evidence['matches']) + 1}",
                event_refs=evidence["event_refs"], requires_rules=required))
        if any(step["rule_id"] == "E09" for step in proof):
            evidence["cautions"].append("计划包含未初始化列统计的诊断变化；已核验物理执行差异，统计质量问题仍需关注。")
    elif not evidence["blockers"] and (not evidence["matches"] or
            evaluate({**evidence, "mandatory_reviews": []})["status"] == "review"):
        from pcc_tool.plan.assessment_cost import estimate_tradeoff
        estimate = estimate_tradeoff(query, old, new, metadata or {}, captures or {})
        if estimate:
            evidence["matches"].append(dict(estimate, id=f"m{len(evidence['matches']) + 1}", event_refs=evidence["event_refs"]))
            evidence["mandatory_reviews"].append(review_reason("D08",
                "A22 的扫描、回表、传输与内存分数来自估算。真实的匹配率、中间行数与资源占用可改变收益方向。"))
    changed_nodes = [n for event in events for n in (old_nodes.get(event.old_node), new_nodes.get(event.new_node)) if n]
    operators = {n.operator for n in changed_nodes}
    for rule in RULE_CATALOG:
        relevant = bool(operators.intersection(rule["operators"]))
        if rule["id"] == "C06":
            relevant = any("tiflash" in n.task or n.task.startswith("mpp") for n in changed_nodes)
        if rule["id"] == "C11":
            relevant = any("runtime filter" in n.attributes.lower() or "runtime_filter" in n.attributes.lower() for n in changed_nodes)
        if relevant and not any(m["rule_id"] == rule["id"] for m in evidence["matches"]):
            evidence["hints"].append(dict(rule_id=rule["id"], reason=rule["requires"]))
    return primitive(evidence)


def access_patterns(a, b, tree, metadata, stats):
    from sqlglot import exp
    # A01: one integer clustered primary-key equality. Index point keys and
    # Batch_Point_Get need richer range decoding and intentionally stay review.
    if a.operator == "TableReader" and b.operator == "Point_Get" and len(a.children) == 1 and tree is not None:
        scan = a.children[0]
        point = re.fullmatch(r"handle:(-?\d+)", b.attributes)
        interval = re.fullmatch(r"range:\[(-?\d+),(-?\d+)\], keep order:false", scan.attributes)
        where = tree.args.get("where")
        condition = where.this if where else None
        pk = metadata["primary_key"]
        if (scan.operator == "TableRangeScan" and point and interval and point[1] == interval[1] == interval[2]
                and len(pk) == 1 and metadata["clustered_pk"] and re.match(r"(?:BIGINT|INT|SMALLINT|TINYINT|MEDIUMINT)\b", metadata["columns"].get(pk[0], ""))
                and isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column) and condition.this.name == pk[0]
                and isinstance(condition.expression, exp.Literal) and not condition.expression.is_string
                and condition.expression.this == point[1] and scan.access == b.access and root_cop(b, scan)
                and a.task == "root" and number(a.estimate) == number(b.estimate) == number(scan.estimate) == 1
                and attrs(a) == "child-reference" and not scan.role and not b.children and not b.role and not a.role):
            return "A01", "DDL 证明整数聚簇主键唯一；相同主键值的单点范围改为 Point_Get。", []
    if a.operator == "IndexLookUp" and b.operator == "IndexReader" and len(a.children) == 2 and len(b.children) == 1 and tree is not None:
        index, table = a.children
        new_index = b.children[0]
        access = re.fullmatch(r"table:([^,]+), index:[^(]+\(([^)]+)\)", index.access)
        if access and index.operator in {"IndexRangeScan", "IndexFullScan"} and same(index, new_index, ignore_role=True):
            available = {c.strip() for c in access[2].split(",")} | (set(metadata["primary_key"]) if metadata["clustered_pk"] else set())
            required = {col.name for col in tree.find_all(exp.Column)}
            if any(isinstance(e, exp.Star) or e.find(exp.Star) for e in tree.expressions):
                required = set(metadata["columns"])
            if (required <= available and table.operator == "TableRowIDScan" and not table.children
                    and table.access == "table:" + access[1] and table.attributes == "keep order:false"
                    and table.task == index.task == "cop[tikv]"
                    and a.attributes == "" and attrs(b) == "child-reference" and a.role == b.role
                    and not a.access and not b.access and a.task == b.task == "root"
                    and number(a.estimate) == number(b.estimate) == number(table.estimate) == number(index.estimate)):
                return "A02", "同一索引范围与候选量，DDL 和 SQL 所需列证明覆盖，无回表过滤；消除回表。", []
    if a.operator == b.operator == "IndexLookUp" and shell(a, b) and len(a.children) == len(b.children) == 2 and stats:
        ai, at = a.children
        bi, bt = b.children
        if (at.operator == bi.operator == "Selection" and len(at.children) == len(bi.children) == 1
                and at.task == bi.task == ai.task == bt.task == "cop[tikv]"
                and shell(at, bi, estimate=True, role=True) and same(ai, bi.children[0], ignore_role=True)
                and same(at.children[0], bt, ignore_estimate=True, ignore_role=True)
                and bt.operator == "TableRowIDScan" and ai.operator in {"IndexRangeScan", "IndexFullScan"}
                and number(at.estimate) == number(bi.estimate) == number(bt.estimate)
                and number(at.children[0].estimate) == number(ai.estimate) and safe_filter(at.attributes, metadata)):
            return "A03", "相同谓词从回表后移动到索引端；索引范围和最终输出不变。", input_guard(ai.estimate, bi.estimate, reduction=True)
    return None


def aggregate_pattern(a, b):
    if a.operator != b.operator or a.operator not in {"HashAgg", "StreamAgg"} or len(a.children) != 1 or len(b.children) != 1:
        return None
    ar, br = a.children[0], b.children[0]
    if ar.operator not in READERS or ar.operator != br.operator or len(ar.children) != 1 or len(br.children) != 1:
        return None
    partial = br.children[0]
    match = re.fullmatch(r"funcs:(count|sum|min|max)\(([^,]+)\)->(Column#\d+)", a.attributes)
    inner = re.fullmatch(r"funcs:(count|sum|min|max)\(([^,]+)\)->(Column#\d+)", partial.attributes)
    if not match or not inner:
        return None
    # TiDB EXPLAIN retains the aggregate name in FinalMode. In particular,
    # COUNT partial states are merged by count(Column#...), not displayed as
    # SQL SUM. Check the physical representation, not an algebraic rewrite.
    final_func = match[1]
    if (partial.operator not in {"HashAgg", "StreamAgg"} or len(partial.children) != 1 or not root_cop(a, partial)
            or (match[1], match[2]) != (inner[1], inner[2]) or "distinct" in match[2]
            or not re.fullmatch(COLUMN + r"|1", match[2])
            or b.attributes != f"funcs:{final_func}({inner[3]})->{match[3]}"
            or partial.access or partial.role or a.access or b.access
            or number(br.estimate) != number(partial.estimate)
            or not shell(ar, br, estimate=True) or not same(ar.children[0], partial.children[0])
            or number(a.estimate) != number(b.estimate) or a.task != b.task or a.role != b.role):
        return None
    guards = input_guard(ar.estimate, br.estimate, minimum=10000)
    guards.append(dict(kind="ratio", input=ar.estimate, output=br.estimate, constant="10", label="A12 最大中间行比例 (%)"))
    return "A12", "相同单个聚合的 partial/final 映射，扫描不变；传输聚合结果替代原始行。", guards
