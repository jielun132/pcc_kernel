"""Evidence that schema-and-statistics replay cannot settle a SQL assessment."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from pcc_tool.capture.guard import guard_sql
from pcc_tool.models import PCCError
from pcc_tool.plan.rule_catalog import RULE_CATALOG

DATA_CARDS = {r["id"]: r for r in RULE_CATALOG if r["group"] in {"data_review", "runtime_note"}}


def review_reason(rule, reason):
    return dict(rule_id=rule, reason=reason, check=DATA_CARDS[rule]["requires"], source=DATA_CARDS[rule]["source"])


def _sources(scope, seen=None):
    """Follow referenced CTEs, derived tables and set operands to physical inputs."""
    seen = set() if seen is None else seen
    if id(scope) in seen:
        return
    seen.add(id(scope))
    for _, source in scope.selected_sources.values():
        if isinstance(source, exp.Table):
            if source.db or source.name.lower() != "dual":
                yield source
        elif isinstance(source, Scope):
            yield from _sources(source, seen)
    for child in (*scope.union_scopes, *scope.subquery_scopes):
        yield from _sources(child, seen)


def _has_column(scope, name, schema, metadata):
    for _, source in scope.selected_sources.values():
        if isinstance(source, exp.Table):
            cols = metadata.get((source.db or schema) + "." + source.name, {}).get("columns", {})
        else:
            cols = source.outer_columns or source.expression.named_selects
        if any(c.casefold() == name.casefold() for c in cols):
            return True
    return False


def _correlated(scope, schema, metadata):
    for col in scope.external_columns:
        if col.table:
            return True
        # sqlglot also lists unqualified *local* columns as external until bound.
        if not _has_column(scope, col.name, schema, metadata):
            parent = scope.parent
            while parent:
                if _has_column(parent, col.name, schema, metadata):
                    return True
                parent = parent.parent
    return False


def _literal_zero_limit(select):
    limit = select.args.get("limit")
    return bool(limit and isinstance(limit.expression, exp.Literal) and limit.expression.this == "0")


def _false_where(query):
    where = query.args.get("where")
    predicate = where.this if where else None
    if isinstance(predicate, exp.Boolean):
        return predicate.this is False
    if isinstance(predicate, exp.Literal):
        return predicate.is_int and predicate.this == "0"
    return (isinstance(predicate, exp.EQ) and all(isinstance(v, exp.Literal) and v.is_int for v in
            (predicate.this, predicate.expression)) and predicate.this.this.lstrip("0") != predicate.expression.this.lstrip("0"))


def _join_may_multiply(scope, join, schema, metadata):
    sources = {k.casefold(): v for k, (_, v) in scope.selected_sources.items()}
    def unique(alias, key):
        table = sources.get(alias.casefold())
        return isinstance(table, exp.Table) and [
            c.casefold() for c in metadata.get((table.db or schema) + "." + table.name, {}).get("primary_key", [])
        ] == [key.casefold()]
    if len(sources) == 2 and join.args.get("using"):
        return not any(unique(alias, key.name) for key in join.args["using"] for alias in sources)
    condition = join.args.get("on")
    if condition is not None and len(sources) == 2:
        for predicate in condition.flatten() if isinstance(condition, exp.And) else (condition,):
            if isinstance(predicate, exp.EQ) and all(isinstance(c, exp.Column) for c in (predicate.this, predicate.expression)):
                left, right = predicate.this, predicate.expression
                if left.table and right.table and left.table.casefold() != right.table.casefold() and (
                        unique(left.table, left.name) or unique(right.table, right.name)):
                    return False
    return True


def _subquery_risk(scope, schema, metadata):
    query = scope.expression
    wrapper = query.parent
    while isinstance(wrapper, (exp.Paren, exp.Subquery)):
        wrapper = wrapper.parent
    if not scope.is_subquery or wrapper is None or _literal_zero_limit(query) or _false_where(query):
        return None
    tables = sorted({t.name for t in _sources(scope)})
    if not tables:
        return None
    source = "内层读取 " + "、".join(tables) + "。"
    projection = query.expressions[0].unalias() if query.expressions else None
    global_agg = isinstance(projection, exp.AggFunc) and not any(
        query.args.get(key) for key in ("group", "having", "offset", "limit"))
    if isinstance(wrapper, exp.Exists) and global_agg:
        return None  # A global aggregate emits one row even on an empty input.
    is_set = isinstance(wrapper, (exp.In, exp.Any, exp.All))
    correlated = _correlated(scope, schema, metadata)
    if is_set:
        op = wrapper.key.upper()
        if isinstance(wrapper.parent, exp.Not):
            op = "NOT " + op
        yield "D04", source + op + " 的候选集大小、重复键和实际匹配比例未知。空集与含 NULL 的集合可能产生不同的谓词结果。"
    if correlated:
        yield "D03", source + "子查询引用外层列。每个外键的重复度、匹配数和去关联后的中间行数需要实际数据。"
    elif isinstance(wrapper, exp.Exists):
        negated = isinstance(wrapper.parent, exp.Not)
        yield "D02", source + ("NOT EXISTS 在空集为真" if negated else "EXISTS 在空集为假") + "。TiDB 可能在优化时求值，存在匹配行时外层分支可能改变。"
    elif not is_set:
        if global_agg and isinstance(projection, exp.Count):
            effect = "无分组 COUNT 在空集为 0，实际计数可改变点查键、范围或输出常量。"
        elif global_agg and isinstance(projection, (exp.Min, exp.Max, exp.Sum, exp.Avg)):
            effect = projection.key.upper() + " 在空集为 NULL，有匹配行时可得到具体值，导致谓词或访问范围改变。"
        elif query.args.get("limit") and isinstance(query.args["limit"].expression, exp.Literal) and query.args["limit"].expression.this == "1":
            effect = "LIMIT 1 在空集得不到行，有数据时所取的值可能改变外层的范围或投影。"
        else:
            effect = "零行可产生 NULL，单行的值依赖样本。若允许返回多行，真实数据还可能触发错误 1242。"
        yield "D01", source + "读表标量子查询在可独立求值时可能被折叠。" + effect


def sql_risks(tree, schema, metadata, kind=None):
    """Return stable rule/reason pairs. Do not archive SQL literals or row data."""
    risks = []
    if isinstance(tree, (exp.Update, exp.Delete, exp.Insert)):
        if kind == "replace" or tree.args.get("conflict") or tree.args.get("ignore"):
            detail = "目标上已有的唯一键决定冲突、忽略、替换或更新分支。"
        elif isinstance(tree, exp.Insert):
            detail = "实际输入和目标唯一键决定写入行数、索引维护量和可能的冲突。"
        else:
            detail = "实际命中行数、相关索引维护和锁等待无法从空表计划中验证。"
        risks.append(("D11", (kind or tree.key).upper() + " 的普通 EXPLAIN 只展示计划。" + detail))
    if tree.find(exp.TableSample):
        risks.append(("D12", "SQL 使用 TABLESAMPLE。以 REGIONS 采样时，结果由实际 Region 边界和其中的数据决定。"))
    if isinstance(tree, (exp.Update, exp.Delete)) and tree.args.get("limit") and (
            tree.args.get("where") or tree.args.get("order")) and not _literal_zero_limit(tree):
        risks.append(("D05", "写语句按条件或顺序选取有限行。实际定位到足够候选行所需的扫描量未知。"))
    for scope in traverse_scope(tree):
        risks.extend(_subquery_risk(scope, schema, metadata))
        select = scope.expression
        if isinstance(select, exp.SetOperation) and (not isinstance(select, exp.Union) or select.args.get("distinct")) and any(_sources(scope)):
            risks.append(("D04", select.key.upper() + " 的输入集合重叠率和重复度会改变合并、筛除及去重规模。"))
        if any(_join_may_multiply(scope, j, schema, metadata) for j in select.args.get("joins") or ()):
            risks.append(("D13", "无法证明连接一侧对每个键至多匹配一行。两侧重复键或非等值条件可能放大中间结果。"))
        for clause in ("where", "order"):
            part = select.args.get(clause)
            if part is not None and part.find(exp.Rand) and any(_sources(scope)):
                risks.append(("D12", "筛选或排序使用 RAND，实际结果和候选规模需要多次观测。"))
        if select.args.get("limit") and not _literal_zero_limit(select) and (
                select.args.get("where") or select.args.get("having") or select.args.get("joins")) and any(_sources(scope)):
            k = select.args["limit"].expression
            bound = "最多取 " + k.this + " 行" if isinstance(k, exp.Literal) and k.is_int else "按绑定的行数截断"
            risks.append(("D05", "SQL 在筛选或连接后" + bound + "。命中行在扫描顺序中的位置、回表量和不足所需行数时的扫描量都未实测。"))
    return list(dict.fromkeys(risks))


def view_evidence(text, schema, metadata):
    try:
        stmt = sqlglot.parse_one(text, read="mysql")
        body = stmt.expression if isinstance(stmt, exp.Create) else None
        if isinstance(body, exp.Query):
            return sql_risks(body, schema, metadata)
    except sqlglot.errors.SqlglotError:
        pass
    return [("D10", "视图定义无法解析，内部是否存在可预求值的读表子查询尚未核实。")]


def _view_risks(query, metadata):
    pending = [tuple(dep) for dep in query.dependencies]
    seen = set()
    while pending:
        dep = pending.pop()
        if dep in seen:
            continue
        seen.add(dep)
        view = metadata.get(".".join(dep), {})
        if view.get("columns") == {} and "view_reviews" not in view:
            yield "D10", "归档中 " + ".".join(dep) + " 缺少可解析的列或视图定义，无法排除隐藏的读表求值。"
        for rule, reason in view.get("view_reviews", ()):
            yield rule, "视图 " + ".".join(dep) + "。" + reason
        pending.extend(tuple(d) for d in view.get("view_dependencies", ()))


def replay_reviews(query, nodes, metadata):
    findings = {}
    def add(rule, reason):
        reasons = findings.setdefault(rule, [])
        if reason not in reasons:
            reasons.append(reason)

    try:
        tree, kind = guard_sql(query.sql_text, allowed=("select", "update", "delete", "insert", "replace"), parameters=query.parameters)
        for rule, reason in sql_risks(tree, query.schema, metadata, kind):
            add(rule, reason)
    except (sqlglot.errors.SqlglotError, PCCError, RecursionError):
        add("D10", "SQL 语法树无法完整解析，无法排除读表求值或运行时依赖。")
    for rule, reason in _view_risks(query, metadata):
        add(rule, reason)
    for side, items in nodes.items():
        by_reason = {}
        for n in items:
            info = n.attributes.lower()
            if n.operator in {"Apply", "IndexJoin", "IndexHashJoin", "IndexMergeJoin"} or "range: decided by" in info:
                by_reason.setdefault("D03", []).append(n.operator)
            if re.search(r"runtime[ _]filter\s*:", info) or any(
                    key.lower().replace("_", "") == "runtimefilter" for key in n.unknown):
                by_reason.setdefault("D06", []).append(n.operator)
            partition = n.access.lower() + " " + info
            if "partitiontablescan:true" in info or re.search(r"partition(?:s)?\s*:\s*(?:access object is determined|decided by|dynamic|runtime)", partition) or (
                    "partition:all" in n.access.lower() and _dynamic_range(n)):
                by_reason.setdefault("D07", []).append(n.operator)
            if n.operator in {"CTE", "CTEFullScan", "CTEStorage"} or (
                    n.operator in {"Window", "OrderedWindow"} and "partition by" in info):
                by_reason.setdefault("D09", []).append(n.operator)
            if n.operator in {"HashAgg", "StreamAgg"} and ("group by:" in info or "distinct " in info) and _reads_table(n):
                by_reason.setdefault("D13", []).append(n.operator)
        details = {"D03": "外侧行数和重复键决定内侧探测次数及返回行数。",
                   "D06": "Build 键的实际覆盖率和过滤器等待时间未知。",
                   "D07": "实际分区取决于运行时的键值。",
                   "D09": "中间数据量、分区倾斜和内存或磁盘占用取决于实际行。",
                   "D13": "分组数和重复键分布会改变聚合及去重的状态大小。"}
        for rule, ops in by_reason.items():
            add(rule, side + " 计划包含 " + "、".join(dict.fromkeys(ops)) + "。" + details[rule])
        if "D01" in findings or "D02" in findings:
            if items and items[0].operator == "TableDual" and "rows:0" in items[0].attributes and len(items) == 1:
                for rule in ("D01", "D02"):
                    if rule in findings:
                        add(rule, side + " 整份计划只剩 TableDual rows:0。")
            elif any(n.operator == "ScalarSubQuery" for n in items):
                for rule in ("D01", "D02"):
                    if rule in findings:
                        add(rule, side + " 计划保留 ScalarSubQuery，实际返回值仍未验证。")
    physical = [n for entries in nodes.values() for n in entries]
    if physical and not any(n.operator in {"Selection", "Apply", "TopN", "Sort"} or "Join" in n.operator
                            or "pushed down filter:" in n.attributes for n in physical):
        findings.pop("D05", None)
    return [review_reason(rule, " ".join(findings[rule])) for rule in sorted(findings)]


def _dynamic_range(node):
    return "range: decided by" in node.attributes.lower() or any(_dynamic_range(c) for c in node.children)


def _reads_table(node):
    return bool(node.access) or any(_reads_table(c) for c in node.children)
