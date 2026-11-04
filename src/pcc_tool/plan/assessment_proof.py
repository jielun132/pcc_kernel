"""Composable, fail-closed proofs over the complete pair of physical plans.

These transformations never change the archived IR or its diff. A proof is
published only when every subtree and field has been checked, and selecting a
subset of its required cards cannot accidentally accept the whole proof.
"""
from __future__ import annotations

import copy
import re

from pcc_tool.plan.assessment import COLUMN, READERS, alias_only, attrs, limits, number, safe_filter, same, split_list, walk


def pure_columns(text):
    parts = split_list(text)
    return bool(parts) and all(re.fullmatch(COLUMN + r"(?:->Column#\d+)?", part) for part in parts)


def step(rule, a, b, reason, direction="equivalent"):
    return dict(rule_id=rule, direction=direction, old_nodes=[a.node_id], new_nodes=[b.node_id], reason=reason, guards=[])


def version_pair(captures):
    return tuple(getattr(captures.get(side), "version", "") for side in ("old", "new"))


def version_verified(captures):
    # Keep release identities distinct. These three upgrade pairs have captured
    # controls for the plan shapes below; individual formatting rules are narrower.
    old, new = version_pair(captures)
    return "v6.5.12" in (old, new) and bool({old, new} & {"v8.5.5", "v8.5.7", "v7.1.9-0.3"})


def column_type(column, metadata):
    parts = column.split(".")
    if len(parts) != 3:
        return None
    return metadata.get(".".join(parts[:2]), {}).get("columns", {}).get(parts[2])


def numeric_equality(text, metadata):
    """Canonicalize just one typed eq, leaving its enclosing join text intact."""
    pattern = r"eq\((" + COLUMN + r"), (" + COLUMN + r")\)"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return None
    match = matches[0]
    types = [column_type(c, metadata) for c in match.groups()]
    if not all(t and re.fullmatch(r"(?:BIGINT|INT|SMALLINT|MEDIUMINT|TINYINT)(?:\(\d+\))?(?: UNSIGNED)?", t) for t in types):
        return None
    return text[:match.start()] + "eq(" + ", ".join(sorted(match.groups())) + ")" + text[match.end():]


def projection_lineage(node):
    """Decode the contiguous slots of an injected aggregate projection.

    v6.5.12 omits pure-column output labels. Computed columns anchor the slot
    sequence; every explicit anchor must agree. v8.5.5 prints those labels.
    This is used only for the paired injected-projection shape below.
    """
    parts = split_list(node.attributes)
    parsed = [re.fullmatch(r"(.+?)(?:->Column#(\d+))?", p) for p in parts]
    if not parts or not all(parsed):
        return None
    bases = {int(m[2]) - i for i, m in enumerate(parsed) if m[2]}
    if len(bases) != 1:
        return None
    base = next(iter(bases))
    return {f"Column#{base + i}": m[1] for i, m in enumerate(parsed)}


def aggregate_lineage(a, b):
    if a.operator != b.operator or a.operator not in {"HashAgg", "StreamAgg"} or len(a.children) != 1 or len(b.children) != 1:
        return None
    x, y = a.children[0], b.children[0]
    if x.operator != y.operator or x.operator != "Projection" or len(x.children) != 1 or len(y.children) != 1:
        return None
    lx, ly = projection_lineage(x), projection_lineage(y)
    if not lx or not ly:
        return None
    def unique_sources(lineage):
        seen, values = set(), []
        for value in lineage.values():
            if re.fullmatch(COLUMN, value):
                if value in seen:
                    continue
                seen.add(value)
            values.append(value)
        return values
    if unique_sources(lx) != unique_sources(ly):
        return None
    def normalize(text, lineage):
        missing = []
        def replace(m):
            if m[0] not in lineage:
                missing.append(m[0])
            return "{" + lineage.get(m[0], m[0]) + "}"
        value = re.sub(r"(?<!->)Column#\d+", replace, text)
        return None if missing else value
    left, right = normalize(a.attributes, lx), normalize(b.attributes, ly)
    if left is None or left != right:
        return None
    aa, bb = copy.deepcopy(a), copy.deepcopy(b)
    aa.attributes = bb.attributes = left
    aa.children[0].attributes = bb.children[0].attributes = str(unique_sources(lx))
    return aa, bb


def prove_plans(query, old, new, metadata, captures):
    if old.parse_status != "ok" or new.parse_status != "ok" or len(old.roots) != len(new.roots):
        return None
    if any(n.unknown for plan in (old, new) for root in plan.roots for n in walk(root)):
        return None
    verified = version_verified(captures)
    versions = version_pair(captures)
    shuffle_context = [[(n.attributes, n.task, n.engine) for r in plan.roots for n in walk(r) if n.operator == "Shuffle"] for plan in (old, new)]
    import sqlglot
    from sqlglot import exp
    aliases, tree = {}, None
    try:
        tree = sqlglot.parse_one(query.sql_text or "", read="mysql")
        for table in tree.find_all(exp.Table):
            aliases[table.alias_or_name] = (table.db or query.schema) + "." + table.name
    except (ValueError, sqlglot.errors.ParseError):
        pass

    def decimal_display(text):
        pattern = r"if\((" + COLUMN + r"), (" + COLUMN + r"), (0(?:\.0+)?)\)"
        def replace(match):
            data_type = column_type(match[2], metadata) or ""
            size = re.fullmatch(r"DECIMAL\((\d+),\s*(\d+)\)", data_type)
            if not size or ("." in match[3] and len(match[3].split(".")[1]) != int(size[2])):
                return match[0]
            return f"if({match[1]}, {match[2]}, decimal-zero)"
        return re.sub(pattern, replace, text)

    def implied_filters(plan):
        """Integer outer PK bound implies the same bound on a dynamic inner key."""
        result = set()
        for root in plan.roots:
            for join in walk(root):
                if join.operator not in {"IndexJoin", "IndexHashJoin", "IndexMergeJoin"} or len(join.children) != 2:
                    continue
                keys = re.search(r"outer key:(" + COLUMN + r"), inner key:(" + COLUMN + r"), equal cond:eq\((" + COLUMN + r"), (" + COLUMN + r")\)$", join.attributes)
                if not keys or set(keys.groups()[:2]) != set(keys.groups()[2:]):
                    continue
                outer, inner = keys[1], keys[2]
                if not all(re.fullmatch(r"(?:BIGINT|INT|SMALLINT|MEDIUMINT|TINYINT)(?:\(\d+\))?(?: UNSIGNED)?", column_type(c, metadata) or "") for c in (outer, inner)):
                    continue
                parts = outer.split(".")
                meta = metadata.get(".".join(parts[:2]), {})
                if not meta.get("clustered_pk") or meta.get("primary_key") != [parts[-1]]:
                    continue
                outer_nodes = list(walk(join.children[0]))
                if join.children[0].role != "Build" or len([n for n in outer_nodes if n.operator.endswith("Scan")]) != 1:
                    continue
                for scan in outer_nodes:
                    bound = re.fullmatch(r"range:\[-inf,(-?\d+)\), keep order:(?:true|false)", scan.attributes)
                    if (scan.operator == "TableRangeScan" and bound and scan.access.startswith("table:")
                            and aliases.get(scan.access[6:]) == ".".join(parts[:2])):
                        result.add((inner, outer, bound[1]))
        return result
    implied = implied_filters(old) & implied_filters(new)

    def collation_display(a, b):
        if not verified or not isinstance(tree, exp.Select) or len(list(tree.find_all(exp.Table))) != 1:
            return False
        condition = tree.args.get("where")
        condition = condition.this if condition else None
        if not isinstance(condition, exp.EQ) or not isinstance(condition.this, exp.Column) or not isinstance(condition.expression, exp.Collate):
            return False
        collate = condition.expression
        if collate.expression.name.lower() != "utf8mb4_general_ci" or not isinstance(collate.this, exp.Literal) or not collate.this.is_string:
            return False
        value = collate.this.this.upper()
        # ASCII letters/digits/hyphen have their uppercase UTF-16 weights in
        # general_ci. No generic stripping of NULs, Unicode or collation guesses.
        if not re.fullmatch(r"[A-Z0-9-]+", value):
            return False
        index = re.fullmatch(r"table:[^,]+, index:[^(]+\(([^),]+)\)", a.access)
        if not index or index[1] != condition.this.name:
            return False
        encoded = "".join(r"\x00" + c for c in value)
        old_info = f'range:["{value}","{value}"], keep order:false'
        new_info = f'range:["{encoded}","{encoded}"], keep order:false'
        return ((a.attributes, b.attributes) == (old_info, new_info) and versions[0] == "v6.5.12"
                or (b.attributes, a.attributes) == (old_info, new_info) and versions[1] == "v6.5.12")

    def long_constant_display(a, b):
        import json
        if not verified or not isinstance(tree, exp.Select) or not query.parameters or len(query.parameters) != 1:
            return False
        condition = tree.args.get("where")
        condition = condition.this if condition else None
        if not isinstance(condition, exp.EQ) or not isinstance(condition.this, exp.Column) or not isinstance(condition.expression, exp.Placeholder):
            return False
        param = query.parameters[0]
        if param.get("type") != "string" or not isinstance(param.get("value"), str):
            return False
        value = param["value"]
        raw = value.encode("utf-8")
        if len(raw) <= 64:
            return False
        try:
            prefix = raw[:64].decode("utf-8")
        except UnicodeDecodeError:
            return False
        tables = list(tree.find_all(exp.Table))
        if len(tables) != 1:
            return False
        column = f"{tables[0].db or query.schema}.{tables[0].name}.{condition.this.name}"
        full = f"eq({column}, {json.dumps(value, ensure_ascii=False)})"
        short = f"eq({column}, {json.dumps(prefix + '...(len:' + str(len(raw)) + ')', ensure_ascii=False)})"
        return ((a.attributes, b.attributes) == (full, short) and versions[1] in {"v8.5.5", "v8.5.7"}
                or (b.attributes, a.attributes) == (full, short) and versions[0] in {"v8.5.5", "v8.5.7"})

    def fixed_access_estimate(a, b, na, nb, child_proofs):
        # Require an observed unchanged access below the changed estimate.
        if a.operator in {"IndexRangeScan", "TableRangeScan"} and not a.children and a.task == "cop[tikv]":
            access = re.fullmatch(r"table:([\w$]+)(?:, index:[\w$]+\(([\w$, ]+)\))?", a.access)
            if not access or a.attributes != b.attributes:
                return False
            info = metadata.get(aliases.get(access[1]), {})
            keys = access[2].split(", ") if access[2] else info.get("primary_key", [])
            if not keys or not info.get("stats_ready") or (not access[2] and not info.get("clustered_pk")):
                return False
            if not all(re.fullmatch(r"(?:BIGINT|INT|SMALLINT|MEDIUMINT|TINYINT|DECIMAL)(?:\([\d, ]+\))?(?: UNSIGNED)?",
                                    info["columns"].get(k, "")) for k in keys):
                return False
            value = r"(?:-?\d+(?:\.\d+)?|[+-]inf)"
            endpoint = " ".join([value] * len(keys))
            return (re.fullmatch(r"range:[\[(]" + endpoint + "," + endpoint + r"[\])], keep order:(?:true|false)", a.attributes)
                    and min(na, nb) > 0 and max(na, nb) <= min(na, nb) * number("1.10"))
        if a.operator == "Selection" and len(a.children) == 1 and a.task == "cop[tikv]":
            scan = a.children[0]
            table = re.fullmatch(r"table:([\w$]+)", scan.access)
            info = metadata.get(aliases.get(table[1]), {}) if table else {}
            # Same deterministic filter after the exact same full scan. No
            # estimate-dependent operator lies between that scan and filter.
            return (scan.operator == "TableFullScan" and scan.attributes == "keep order:false"
                    and info.get("stats_ready") and same(scan, b.children[0]) and safe_filter(a.attributes, info)
                    and a.attributes == b.attributes and number(scan.estimate) is not None
                    and 0 <= min(na, nb) <= max(na, nb) <= number(scan.estimate))
        if not any(p["rule_id"] == "E16" for p in child_proofs):
            return False
        if a.operator == "Projection" and not (pure_columns(a.attributes) and pure_columns(b.attributes)):
            return False
        if a.operator in READERS | {"Projection"} and len(a.children) == 1:
            return na == number(a.children[0].estimate) and nb == number(b.children[0].estimate)
        return False

    def compare(a, b):
        if same(a, b):
            return []
        # Root projection becomes a TiKV projection. v8.5.5's planner admits a
        # pure-column pushdown only when colRefCount < inputSchemaLen.
        for left, right, side, direction in ((a, b, 1, "better"), (b, a, 0, "worse")):
            if (left.operator == "Projection" and left.task == "root" and pure_columns(left.attributes)
                    and len(left.children) == 1 and versions[side] in {"v8.5.5", "v8.5.7"}):
                reader = left.children[0]
                if (reader.operator in READERS and reader.task == "root" and len(reader.children) == 1
                        and reader.children[0].task == "cop[tikv]" and right.operator == reader.operator
                        and len(right.children) == 1 and right.children[0].operator == "Projection"
                        and right.children[0].task == "cop[tikv]" and pure_columns(right.children[0].attributes)):
                    moved = copy.deepcopy(left)
                    outer = moved.children[0]
                    moved.children = outer.children
                    moved.task, moved.engine = "cop[tikv]", "tikv"
                    outer.children = [moved]
                    outer.role, moved.role = moved.role, outer.role
                    proof = compare(outer, right) if direction == "better" else compare(right, outer)
                    if proof is not None:
                        return [step("A07", a, b, f"相同纯列投影移过 Reader 边界，其余子树逐项核验。{versions[side]} 对纯列裁剪要求输出列数小于输入列数，减少传输列。小输入收益可能很小。" if direction == "better" else
                                     "相同纯列裁剪从 TiKV 移回 root；其余子树逐项核验，跨 Reader 传输列增加。", direction)] + proof

        # The ordered union of equality-prefix indexes emits clustered handles
        # in primary-key order; the same limit can be embedded in IndexMerge.
        for left, right, direction in ((a, b, "better"), (b, a, "worse")):
            if verified and left.operator == "TopN" and len(left.children) == 1 and right.operator == "IndexMerge":
                merge, bound = left.children[0], limits(left)
                if merge.operator == "IndexMerge" and bound and bound[2] > 0 and not left.unknown:
                    key = bound[0].split(".")
                    meta = metadata.get(".".join(key[:2]), {}) if len(key) == 3 else {}
                    if (meta.get("clustered_pk") and meta.get("primary_key") == key[2:]
                            and merge.attributes == "type: union" and right.attributes == f"type: union, limit embedded(offset:{bound[1]}, count:{bound[2]})"
                            and len(merge.children) >= 3 and merge.children[-1].operator == "TableRowIDScan"):
                        scans = [n for c in merge.children[:-1] for n in walk(c) if n.operator.endswith("Scan")]
                        ordered = bool(scans) and all(n.operator == "IndexRangeScan" and re.fullmatch(
                            r"range:\[(-?\d+),\1\], keep order:true", n.attributes) and re.fullmatch(
                            r"table:" + re.escape(key[-2]) + r", index:[^(]+\([A-Za-z_][\w$]*\)", n.access) for n in scans)
                        if ordered:
                            adjusted = copy.deepcopy(right)
                            adjusted.attributes = "type: union"
                            proof = compare(merge, adjusted) if direction == "better" else compare(adjusted, merge)
                            if proof is not None and (left.task, left.engine, left.role, left.access) == (merge.task, merge.engine, merge.role, merge.access):
                                return [step("A10", a, b, "聚簇主键顺序由等值索引前缀和有序 IndexMerge 保证；相同 offset/count 内嵌，省去额外 TopN。" if direction == "better" else "反向增加相同主键顺序上的 TopN。", direction)] + proof

        if verified:
            if {a.operator, b.operator} == {"HashAgg", "StreamAgg"} and a.attributes.startswith("group by:"):
                def empty_input(n):
                    if len(n.children) != 1:
                        return False
                    source = n.children[0]
                    if source.operator == "Projection" and len(source.children) == 1:
                        source = source.children[0]
                    return source.operator == "TableDual" and source.attributes == "rows:0" and not source.children
                if empty_input(a) and empty_input(b) and a.task == b.task == "root":
                    aligned = copy.copy(b)
                    aligned.operator = a.operator
                    proof = compare(a, aligned)
                    if proof is not None:
                        return [step("E17", a, b, "两端分组输入都是 rows:0 的 TableDual，核对了分组键、聚合输出和投影列。两种聚合均无需处理输入行。")] + proof
            mapped = aggregate_lineage(a, b)
            if mapped and (a.attributes != mapped[0].attributes or b.attributes != mapped[1].attributes):
                proof = compare(*mapped)
                if proof is not None:
                    return [step("E06", a, b, "已核对聚合投影的输出槽位、来源表达式、GROUP BY 与每个聚合输入；仅重复纯列消除及编号重映射，计算表达式和聚合输出未变。")]+proof
            # Virtual receiver is a named explanation boundary, paired with an
            # otherwise identical Shuffle / Window / Sort subtree.
            for left, right, reverse in ((a, b, False), (b, a, True)):
                if (shuffle_context[0] and shuffle_context[0] == shuffle_context[1]
                        and right.operator == "ShuffleReceiver" and len(right.children) == 1 and not right.attributes
                        and not right.access and not right.unknown and right.task == left.task == "root"
                        and right.role == left.role and number(right.estimate) == number(left.estimate)):
                    proof = compare(right.children[0], left) if reverse else compare(left, right.children[0])
                    if proof is not None:
                        return [step("E15", a, b, "已核验相同 Shuffle 上下文与输入；仅 EXPLAIN 增加 ShuffleReceiver 接收边界，分片并发与排序不变。")]+proof

        for left, right, reverse in ((a, b, False), (b, a, True)):
            # Redundant correlated filter: compare the physical range and
            # output cardinality, not the parameterized leaf's new estRows.
            if right.operator == "Selection" and len(right.children) == 1 and left.operator == "IndexRangeScan":
                scan = right.children[0]
                for inner, outer, bound in implied:
                    if (right.attributes == f"lt({inner}, {bound})" and left.attributes == f"range: decided by [eq({inner}, {outer})], keep order:false"
                            and right.task == left.task == scan.task == "cop[tikv]" and right.engine == left.engine
                            and not right.access and not right.unknown and right.role == left.role
                            and not scan.role and number(right.estimate) == number(left.estimate) and same(left, scan, ignore_estimate=True, ignore_role=True)):
                        return [step("E07", a, b, "外侧整数聚簇主键上界与动态等值索引范围已蕴含相同过滤；范围、回表候选不变。叶子相关参数估算口径变化不当成额外实扫。")]
            # A unique inner PK means the outer join cannot multiply rows.
            if left.operator == "Limit" and len(left.children) == 1 and right.operator in {"IndexJoin", "IndexHashJoin"}:
                bound = limits(left)
                key = re.search(r"inner key:(" + COLUMN + r"), equal cond:eq\((" + COLUMN + r"), (" + COLUMN + r")\)$", right.attributes)
                if bound and not bound[0] and bound[1] == 0 and key and key[1] in {key[2], key[3]} and len(right.children) == 2:
                    parts = key[1].split(".")
                    meta = metadata.get(".".join(parts[:2]), {})
                    outer, inner = right.children
                    if ("outer join," in right.attributes and meta.get("primary_key") == [parts[-1]] and meta.get("clustered_pk")
                            and outer.role == "Build" and inner.role == "Probe" and inner.operator == "TableReader"
                            and len(inner.children) == 1 and inner.children[0].operator == "TableRangeScan"
                            and outer.attributes == f"limit embedded(offset:0, count:{bound[2]})"
                            and left.role == right.role and not left.access and not left.unknown):
                        proof = compare(right, left.children[0]) if reverse else compare(left.children[0], right)
                        if proof is not None:
                            return [step("E10", a, b, "外连接内侧是唯一聚簇主键点匹配，不放大外侧行数；外侧已内嵌相同 Limit，顶层截断冗余。")]+proof

        if a.operator != b.operator or len(a.children) != len(b.children):
            return None
        if (a.task, a.engine, a.access, a.role, a.unknown) != (b.task, b.engine, b.access, b.role, b.unknown):
            return None
        proofs = []
        aa, bb = attrs(a), attrs(b)
        if aa != bb:
            rule, reason, direction = None, "", "equivalent"
            if alias_only(a, b):
                rule, reason = "E01", "同一位置的相同来源列，仅增加或省略输出别名标记。"
            elif a.operator == "Projection" and decimal_display(aa) == decimal_display(bb):
                rule, reason = "E08", "同一 DECIMAL 列决定 IF 分支类型；零常量仅补足与 DDL 相符的小数位，其他表达式逐字相同。"
            elif a.operator == "IndexRangeScan" and collation_display(a, b):
                rule, reason = "E12", "SQL 显式使用 utf8mb4_general_ci；ASCII 等值端点逐字核对为相同 UTF-16 大写权重键，索引列、开闭边界和顺序不变。"
            elif a.operator == "Selection" and long_constant_display(a, b):
                rule, reason = "E14", "完整绑定字符串与未缩略的计划逐字相符，另一端精确显示前 64 字节和原 UTF-8 字节总数。"
            elif a.operator in {"TableFullScan", "TableRangeScan", "IndexFullScan", "IndexRangeScan"} and re.sub(r", stats:partial\[[A-Za-z_][\w$]*:unInitialized(?:, [A-Za-z_][\w$]*:unInitialized)*\]$", "", aa) == re.sub(r", stats:partial\[[A-Za-z_][\w$]*:unInitialized(?:, [A-Za-z_][\w$]*:unInitialized)*\]$", "", bb):
                rule, reason = "E09", "仅统计诊断字段变化，实际访问路径、范围、估算和执行位置不变。警告：仍存在未初始化的列统计，不代表统计质量已改善。"
            elif a.operator in {"HashJoin", "MergeJoin"} and numeric_equality(aa, metadata) is not None and numeric_equality(aa, metadata) == numeric_equality(bb, metadata):
                rule, reason = "E03", "同类型整数列的等式左右交换，连接语义、角色和输入不变。"
            elif a.operator == "HashJoin":
                for x, y, d in ((aa, bb, "better"), (bb, aa, "worse")):
                    for prefix, target, function, key in (("CARTESIAN anti semi join", "Null-aware anti semi join", "eq", "A20"),
                                                           ("CARTESIAN semi join", "semi join", "nulleq", "A21")):
                        match = re.fullmatch(re.escape(prefix) + r", other cond:(" + function + r"\(" + COLUMN + r", " + COLUMN + r"\))", x)
                        if match and y == target + ", equal:[" + match[1] + "]":
                            rule, direction = key, d
                            reason = "相同输入、角色和条件由笛卡尔候选过滤转为可用哈希键的半连接；保留 NULL 感知/NULL 安全语义。" if d == "better" else "相同条件退回无哈希键的笛卡尔候选过滤。"
            if not rule:
                return None
            proofs.append(step(rule, a, b, reason, direction))
        child_proofs = []
        for left, right in zip(a.children, b.children):
            proof = compare(left, right)
            if proof is None:
                return None
            child_proofs.extend(proof)
        na, nb = number(a.estimate), number(b.estimate)
        if na != nb:
            if not verified or na is None or nb is None:
                return None
            # Only a source-verified sub-one floor and its transparent
            # propagation are accepted, never arbitrary estimate fluctuations.
            floor = min(na, nb) < 1 and max(na, nb) == 1
            if a.operator == "TableDual" and a.attributes == "rows:0" and max(na, nb) <= 1:
                floor = True
            inherited = bool(child_proofs) and any(p["rule_id"] == "E11" for p in child_proofs)
            if inherited and a.operator == "Selection" and min(na, nb) == 0 and max(na, nb) < 1:
                floor = True
            if inherited and a.operator in {"Projection", "TableReader", "IndexReader", "Union"}:
                left_rows = [number(c.estimate) for c in a.children]
                right_rows = [number(c.estimate) for c in b.children]
                if all(n is not None for n in left_rows + right_rows):
                    delta = sum(right_rows) - sum(left_rows)
                    floor = floor or abs((nb - na) - delta) <= number("0.01") * len(left_rows)
            if inherited and a.operator == "IndexLookUp" and a.children and na == number(a.children[-1].estimate) and nb == number(b.children[-1].estimate):
                floor = True
            diagnostic = any(p["rule_id"] == "E09" for p in child_proofs)
            if diagnostic and a.operator in {"Projection", "TableReader", "IndexReader", "Selection"} and len(a.children) == 1:
                input_a, input_b = number(a.children[0].estimate), number(b.children[0].estimate)
                if input_a is not None and input_b is not None and na <= input_a and nb <= input_b:
                    proofs.append(step("E09", a, b, "相同物理扫描和谓词上，未初始化列统计诊断伴随估算口径变化；保留原始估算和统计警告，不据此推断实际扫描量。"))
                    return proofs + child_proofs
            if not floor and fixed_access_estimate(a, b, na, nb, child_proofs):
                proofs.append(step("E16", a, b, f"两端的静态数值扫描范围或全扫描后的整数过滤相同。仅输出估算及其透传变化 {a.estimate} → {b.estimate}，保留两端估算。"))
                return proofs + child_proofs
            if not floor:
                return None
            proofs.append(step("E11", a, b, f"物理结构与表达式已另行完整核对。{versions[0]} / {versions[1]} 的亚一行估算及其传播为 {a.estimate} → {b.estimate}。"))
        return proofs + child_proofs

    result = []
    for a, b in zip(old.roots, new.roots):
        proof = compare(a, b)
        if proof is None:
            return None
        result.extend(proof)
    return result or None
