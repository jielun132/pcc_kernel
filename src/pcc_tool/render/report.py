import csv
import html
import io
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from pcc_tool.artifacts.store import write_bytes
from pcc_tool.models import canonical, primitive
from pcc_tool.render.findings import comparison_method, findings_panel, grouping_panel, unavailable_reason
from pcc_tool.render.navigation import NAV_STYLES, SCRIPT, navigation_markup, plan_copy_text, plan_group

STATUS_LABELS = {"same": "执行计划一致", "changed": "存在差异", "incomparable": "无法比对", "not_run": "无法比对"}
EVENT_LABELS = {"node_added": "新增算子", "node_removed": "移除算子", "operator_changed": "算子变化",
                "access_path_changed": "访问路径变化", "join_changed": "Join / 角色变化", "child_order_changed": "子节点顺序变化",
                "task_or_engine_changed": "任务或引擎变化", "predicate_or_attribute_changed": "谓词或属性变化",
                "estimate_changed": "估算行数变化", "unknown_field_changed": "未知字段变化", "ambiguous_match": "匹配有歧义"}
METRIC_LABELS = {"execution_count": "执行次数", "total_latency": "总耗时", "average_latency": "平均耗时",
                 "total_keys": "总扫描 Key 数（估算）", "average_keys": "平均扫描 Key 数（估算）"}
SESSION_LABELS = {"time_zone": "时区", "sql_mode": "SQL 模式", "character_set_connection": "连接字符集",
                  "collation_connection": "连接排序规则", "tidb_isolation_read_engines": "允许读取的引擎",
                  "tidb_allow_mpp": "允许 MPP", "tidb_enforce_mpp": "强制 MPP",
                  "tidb_use_plan_baselines": "使用执行计划绑定", "tidb_enable_index_merge": "允许 Index Merge",
                  "tidb_enable_plan_cache_for_param_limit": "参数化 LIMIT 的计划缓存",
                  "tidb_stats_load_sync_wait": "统计信息同步加载等待时间", "tidb_cost_model_version": "代价模型版本"}
ISSUE_LABELS = {"NEEDS_EXECUTABLE_SAMPLE": "需要补充可执行样例", "CONTEXT_DIFFERENCE": "两端环境设置不同",
                "DB_QUERY_FAILED": "数据库获取计划时报错",
                "INSTANCE_NO_VISIBLE_ROWS": "源端节点没有可对应的采集记录", "STATS_NOT_READY": "统计信息尚未就绪",
                "DEPENDENCY_MISSING": "缺少依赖对象", "SCHEMA_MISMATCH": "目标表结构与来源不一致",
                "SCHEMA_DRIFT": "表结构发生变化", "STATS_DRIFT": "统计信息发生变化",
                "PLAN_PARSE_FAILED": "计划解析失败", "PLAN_SEMANTICS_UNKNOWN": "计划含未识别信息",
                "PLAN_MATCH_AMBIGUOUS": "计划节点无法唯一对应", "MATERIAL_VERIFICATION_FAILED": "材料核验失败",
                "EXPLAIN_TIMEOUT": "获取计划超时", "INVALID_PARAMETERS": "参数类型或取值无效",
                "SQL_UNSUPPORTED": "SQL 语法尚不支持", "SQL_REJECTED": "SQL 不在允许范围内"}
FIELD_LABELS = {"selection": "筛选条件", "context": "采集环境", "collection": "采集信息", "summary": "比较摘要",
                "issues": "问题记录", "fingerprint": "比较指纹", "begin": "开始时间", "end": "结束时间",
                "schemas": "筛选库", "statement_types": "语句类型", "ranking_scope": "排名范围", "top_n": "每项取前几名",
                "order_by": "排名指标", "rankings": "排名明细", "metrics": "指标", "ranks": "排名",
                "version": "版本", "capture_session": "采集会话", "historical_session": "历史会话",
                "captured_at": "采集时间", "variables": "系统变量", "status_identity": "Status 节点身份",
                "node": "节点", "bindings": "执行计划绑定", "tiflash": "TiFlash 副本", "epochs": "节点启动时间",
                "coverage": "覆盖范围", "window_semantics": "时间窗口口径", "stats_time_semantics": "统计信息时间口径",
                "code": "问题代码", "message": "说明", "query_id": "SQL 标识", "side": "涉及端",
                "severity": "级别", "old": "升级前", "new": "升级后", "object": "对象"}


def escaped(value):
    text = html.escape(str(value if isinstance(value, str) else canonical(value)), quote=True)
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def csv_safe(value):
    value = str(value if value is not None else "")
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def tree_text(ir):
    lines = []

    def visit(node, depth=0):
        lines.append("  " * depth + f"{node.operator} {node.role} | {node.task} | {node.access} | estRows={node.estimate or 'N/A'}")
        if node.attributes:
            lines.append("  " * (depth + 1) + node.attributes)
        if node.unknown:
            lines.append("  " * (depth + 1) + "unknown: " + canonical(node.unknown))
        for child in node.children:
            visit(child, depth + 1)

    if ir:
        for node in ir.roots:
            visit(node)
    return "\n".join(lines) or "没有可用计划"


def highlighted_pair(old, new):
    """Escape all input before marking the changed text; never interpret plan text as HTML."""
    left = old if isinstance(old, str) else canonical(old)
    right = new if isinstance(new, str) else canonical(new)
    if left == right:
        return escaped(left), escaped(right)
    if max(len(left), len(right)) > 10000:
        return f'<mark class="removed">{escaped(left)}</mark>', f'<mark class="added">{escaped(right)}</mark>'
    before, after = [], []
    for kind, a, b, c, d in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        ltext, rtext = escaped(left[a:b]), escaped(right[c:d])
        before.append(ltext if kind == "equal" or not ltext else f'<mark class="removed">{ltext}</mark>')
        after.append(rtext if kind == "equal" or not rtext else f'<mark class="added">{rtext}</mark>')
    return "".join(before), "".join(after)


def plan_rows(ir):
    rows = []

    def visit(node, prefix="", branch=""):
        rows.append((node, prefix + branch))
        indent = prefix + ("  " if branch == "└─" else "│ " if branch else "")
        for i, child in enumerate(node.children):
            visit(child, indent, "└─" if i == len(node.children) - 1 else "├─")

    if ir:
        for node in ir.roots:
            visit(node)
    return rows


def aligned_rows(left, right, events):
    """Align presentation using recorded matches, without inventing new diff events."""
    identities = {"old": {}, "new": {}}
    for event in events:
        if event.old_node is not None and event.new_node is not None:
            token = ("pair", event.old_node, event.new_node)
            identities["old"][event.old_node] = token
            identities["new"][event.new_node] = token
        elif event.type in {"node_added", "node_removed"}:
            side = "new" if event.type == "node_added" else "old"
            identities[side][getattr(event, side + "_node")] = (side, getattr(event, side + "_node"))

    def key(row, side):
        node, _ = row
        return identities[side].get(node.node_id, ("value", canonical([
            node.operator, node.role, node.task, node.engine, node.access,
            node.estimate, node.attributes, node.unknown])))

    a, b = [key(row, "old") for row in left], [key(row, "new") for row in right]
    if a == b:
        return list(zip(left, right))
    rows = []
    # Large repetitive plans can otherwise make SequenceMatcher quadratic.
    for kind, i, j, k, l in SequenceMatcher(None, a, b, autojunk=max(len(a), len(b)) > 2000).get_opcodes():
        if kind == "equal":
            rows.extend(zip(left[i:j], right[k:l]))
        else:
            rows.extend((row, None) for row in left[i:j])
            rows.extend((None, row) for row in right[k:l])
    return rows


def plan_table(query_id, irs, snapshots, events, anchor):
    sides = ("old", "new")
    rows = {side: plan_rows(irs.get((query_id, side))) for side in sides}
    nodes = {side: {node.node_id: node for node, _ in rows[side]} for side in sides}
    marks = {side: {} for side in sides}
    labels = {side: {} for side in sides}
    structural = {side: set() for side in sides}
    fields = {"operator": ("operator",), "access": ("access",), "task/engine": ("task", "engine"),
              "join/role": ("operator", "role", "attributes"), "operator_info": ("attributes",),
              "estRows": ("estimate",), "unknown": ("unknown",)}
    for index, event in enumerate(events, 1):
        for side in sides:
            node_id = getattr(event, side + "_node")
            if node_id in nodes[side]:
                labels[side].setdefault(node_id, []).append((index, EVENT_LABELS.get(event.type, event.type)))
                if event.type in {"node_added", "node_removed"}:
                    structural[side].add(node_id)
        old, new = nodes["old"].get(event.old_node), nodes["new"].get(event.new_node)
        if old and new:
            for field in fields.get(event.field, ()):
                before, after = getattr(old, field), getattr(new, field)
                if before != after:
                    values = highlighted_pair(before, after)
                    for side, node, value in zip(sides, (old, new), values):
                        marks[side].setdefault(node.node_id, {})[field] = value

    def cell(row, side):
        if row is None:
            return '<td class="plan-gap" aria-label="此侧无对应行">—</td>'
        node, prefix = row
        changes = marks[side].get(node.node_id, {})
        tags = labels[side].get(node.node_id, [])
        def value(field, fallback="—"):
            raw = getattr(node, field)
            return changes.get(field, escaped(raw if raw is not None and raw != "" else fallback))
        badges = "".join(f'<a class="node-change" href="#{anchor}-diff-{i}" title="字段 {i} · {escaped(label)}" aria-label="字段 {i} · {escaped(label)}">Δ{i}</a>' for i, label in tags)
        role = f' <span class="role">{value("role")}</span>' if node.role or "role" in changes else ""
        unknown = f'<div class="node-unknown"><small>其他字段</small> {value("unknown")}</div>' if node.unknown or "unknown" in changes else ""
        # Keep unchanged long expressions collapsed; changed fields are visible immediately.
        info = ""
        if node.attributes or "attributes" in changes or unknown:
            default_open = bool(changes.get("attributes") or unknown or node.node_id in structural[side])
            expanded = ' open' if default_open else ''
            info = (f'<details class="node-details"{expanded} data-default-open="{str(default_open).lower()}"><summary>详情</summary>'
                    f'<div class="node-info">{value("attributes")}</div>{unknown}</details>')
        engine = f' · {value("engine")}' if "engine" in changes else ''
        depth = len(prefix) // 2
        access = f'<div class="node-object"><code>{value("access")}</code></div>' if node.access or 'access' in changes else ''
        return (f'<td id="{anchor}-{side}-{escaped(node.node_id)}" class="plan-cell {side}{" changed-cell" if tags else ""}">'
                f'<div class="node-block" data-depth="{depth}" style="--depth:{depth}">'
                f'<div class="node-line"><span class="node-depth">L{depth}</span><code class="operator"><span class="tree-prefix">{escaped(prefix[-2:])}</span>{value("operator")}{role}</code>'
                f'<span class="node-task">{value("task")}{engine}</span>'
                f'<span class="node-estimate"><small>estRows</small> {value("estimate")}</span></div>'
                f'{access}<span class="node-events">{badges}</span>{info}</div></td>')

    heads = []
    for side, label in (("old", "升级前"), ("new", "升级后")):
        snap = snapshots.get((query_id, side))
        heads.append(f'<th scope="col"><div class="plan-column-heading"><span class="side-label">{label}</span>'
                     f'<span class="version">{escaped(snap.version if snap else "未采集")}</span>'
                     f'<button type="button" class="copy-plan" data-copy-plan="{side}" aria-label="复制{label}计划" title="复制完整计划文本">复制</button></div></th>')
    pairs = aligned_rows(rows["old"], rows["new"], events)
    body = "".join('<tr>' + cell(left, "old") + cell(right, "new") + '</tr>' for left, right in pairs)
    if not pairs:
        body = '<tr><td colspan="2" class="no-plan">没有可用计划，请查看本条 SQL 的问题说明。</td></tr>'
    depth = max((len(prefix) // 2 for side in sides for _, prefix in rows[side]), default=0)
    width = max(1000, 2 * (depth * 24 + 360))
    return f'<div class="plan-scroll" tabindex="0" aria-label="执行计划，可滚动"><table class="plan-table" style="min-width:{width}px"><thead><tr>' + "".join(heads) + '</tr></thead><tbody>' + body + '</tbody></table></div>'


def _overview_value(value):
    if value is None:
        return "未记录"
    if value is True:
        return "是"
    if value is False:
        return "否"
    if value == "":
        return "空字符串"
    if isinstance(value, (list, tuple)):
        return "、".join(_overview_value(v) for v in value) if value else "未指定"
    if isinstance(value, dict):
        return "见详细字段"
    return str(value)


def _data_tree(value):
    """Render all supplementary data as expandable fields rather than a JSON dump."""
    if isinstance(value, dict):
        if not value:
            return '<span class="data-empty">无字段</span>'
        rows = "".join(f'<div><dt>{escaped(FIELD_LABELS.get(str(k), str(k)))}</dt><dd>{_data_tree(v)}</dd></div>'
                       for k, v in value.items())
        return f'<details class="data-branch"><summary>{len(value)} 个字段</summary><dl class="data-tree">{rows}</dl></details>'
    if isinstance(value, (list, tuple)):
        if not value:
            return '<span class="data-empty">无记录</span>'
        if all(not isinstance(v, (dict, list, tuple)) for v in value):
            return f'<span>{escaped(_overview_value(value))}</span>'
        rows = "".join(f'<li><span>第 {i} 条</span>{_data_tree(v)}</li>' for i, v in enumerate(value, 1))
        return f'<details class="data-branch"><summary>{len(value)} 条记录</summary><ol class="data-items">{rows}</ol></details>'
    return f'<span>{escaped(_overview_value(value))}</span>'


def _data_details(label, value):
    return f'<details class="data-extra"><summary>{escaped(label)}</summary>{_data_tree(primitive(value))}</details>'


def _overview_card(label, value):
    return f'<div class="overview-card"><dt>{escaped(label)}</dt><dd>{escaped(_overview_value(value))}</dd></div>'


def _versions(snapshots, side):
    versions = sorted({snapshot.version for (_, item_side), snapshot in snapshots.items()
                       if item_side == side and snapshot and snapshot.version and snapshot.version != "unknown"})
    return "、".join(versions) if versions else "未获取"


def _display_time(value):
    text = str(value)
    return text[:10] + " " + text[11:] if len(text) > 10 and text[10] == "T" else text


def _setting_value(value):
    if isinstance(value, str) and value.upper() in {"ON", "OFF"}:
        return ("开启" if value.upper() == "ON" else "关闭") + f"（{value}）"
    return _overview_value(value)


def _percentage(value):
    if value is None:
        return "不可计算"
    try:
        return f"{Decimal(value) * 100:.2f}%"
    except (InvalidOperation, TypeError, ValueError):
        return _overview_value(value)


def _issue_groups(issues, query_positions, query_by_id):
    groups = {}
    for issue in issues:
        issue = issue if isinstance(issue, dict) else {"message": str(issue)}
        key = (str(issue.get("code") or "OTHER"), str(issue.get("message") or ""))
        groups.setdefault(key, []).append(issue)
    if not groups:
        return '<p class="overview-empty">没有记录到问题。</p>'
    output = []
    for (code, message), entries in groups.items():
        qids = list(dict.fromkeys(i.get("query_id") for i in entries if i.get("query_id")))
        severity = "error" if any(i.get("severity") == "error" for i in entries) else "warning"
        title = ISSUE_LABELS.get(code, "其他问题")
        count = f"涉及 {len(qids)} 条 SQL · {len(entries)} 条记录" if qids else f"{len(entries)} 条记录"
        links = []
        for qid in qids:
            if qid in query_positions:
                index = query_positions[qid]
                links.append(f'<a class="query-link" data-query-link href="#query-{index}">SQL #{index:02d}</a>')
            else:
                links.append(f'<span class="query-link">{escaped(qid)}</span>')
        query_links = f'<div class="issue-queries">{"".join(links)}</div>' if links else ""
        instances = list(dict.fromkeys(str(i["instance"]) for i in entries if i.get("instance")))
        objects = list(dict.fromkeys(_overview_value(i["object"]) for i in entries if i.get("object")))
        locations = "".join(f'<p class="issue-location">{label}：{escaped("、".join(values))}</p>'
                            for label, values in (("节点", instances), ("对象", objects)) if values)
        examples = []
        if code == "NEEDS_EXECUTABLE_SAMPLE" and len(qids) <= 3:
            for qid in qids:
                query = query_by_id.get(qid)
                if query and query.sql_text:
                    sql = query.sql_text.strip()
                    if len(sql) > 240:
                        sql = sql[:240] + "…"
                    examples.append(f'<p class="issue-example"><span>已记录的 SQL</span><code>{escaped(sql)}</code></p>')
        differences = {}
        if code == "CONTEXT_DIFFERENCE":
            for issue in entries:
                old, new = issue.get("old"), issue.get("new")
                if not isinstance(old, dict) or not isinstance(new, dict):
                    continue
                for key in sorted(set(old) | set(new)):
                    if old.get(key) != new.get(key):
                        pair = (str(key), canonical(old.get(key)), canonical(new.get(key)))
                        difference = differences.setdefault(pair, {"key": key, "old": old.get(key),
                                                                   "new": new.get(key), "queries": []})
                        qid = issue.get("query_id")
                        if qid and qid not in difference["queries"]:
                            difference["queries"].append(qid)
        compare = ""
        if differences:
            rows = []
            for difference in differences.values():
                references = [f'#{query_positions[qid]:02d}' if qid in query_positions else qid
                              for qid in difference["queries"]]
                key = difference["key"]
                label = SESSION_LABELS.get(key)
                name = f'{escaped(label)}<code>{escaped(key)}</code>' if label else escaped(key)
                rows.append(f'<tr><th scope="row">{name}</th>'
                            f'<td>{escaped(_setting_value(difference["old"]))}</td>'
                            f'<td>{escaped(_setting_value(difference["new"]))}</td>'
                            f'<td>{escaped("、".join(references) if references else "未指定")}</td></tr>')
            compare = ('<div class="overview-table-wrap"><table class="overview-table"><thead><tr><th>设置项</th>'
                       '<th>升级前</th><th>升级后</th><th>涉及 SQL</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')
        if code == "INSTANCE_NO_VISIBLE_ROWS":
            note = '<p class="issue-help">这条提醒不能单独证明缺采；请同时核对节点地址、采集权限和是否存在流量。</p>'
        elif code == "NEEDS_EXECUTABLE_SAMPLE":
            has_text = bool(qids) and all(query_by_id.get(qid) and query_by_id[qid].sql_text for qid in qids)
            explanation = ("已记录 SQL 文本，仍需核对语句完整性、参数或会话上下文。" if has_text
                           else "可能是 SQL 文本、参数或会话上下文不足；请查看对应 SQL 和具体原因。")
            note = f'<p class="issue-help">{explanation}</p>'
        else:
            note = ""
        output.append(f'<section class="overview-issue {severity}"><div class="issue-heading"><strong>{escaped(title)}</strong>'
                      f'<span>{escaped(count)}</span></div><p>{escaped(message or "未记录具体原因")}</p>{note}'
                      f'{locations}{query_links}{"".join(examples)}{compare}<p class="issue-code">问题代码：{escaped(code)}</p>'
                      f'{_data_details("查看该问题的详细记录", entries)}</section>')
    return "".join(output)


def report_overview(selection, context, result, queries, snapshots):
    selection, context = selection or {}, context or {}
    summary, issues = result.summary, result.issues
    query_positions = {item["query_id"]: index for index, item in enumerate(result.items, 1)}
    query_by_id = {query.query_id: query for query in queries}
    collection = context.get("collection") or {}
    begin, end = selection.get("begin"), selection.get("end")
    time_range = f'{_display_time(begin)} 至 {_display_time(end)}' if begin and end else "未指定或未记录"
    kind = selection.get("kind")
    scope = {"global": "全局排名", "per_schema": "按库分别排名"}.get(selection.get("ranking_scope"))
    if kind == "explicit_samples":
        method = "使用指定 SQL 样例，不重新排名"
    elif kind == "legacy_sample_set":
        method = "使用旧材料包中的样例，不重新排名"
    elif scope:
        method = scope + (f'，每项取前 {selection["top_n"]} 名' if selection.get("top_n") is not None else "")
    else:
        method = "未记录"
    metrics = [METRIC_LABELS.get(key, key) for key in selection.get("order_by", [])]
    origins = [origin for query in queries for origin in getattr(query, "sample_origins", []) or []]
    clusters = sorted({str(origin["source_cluster"]) for origin in origins if origin.get("source_cluster")}
                      | {str(query.source_cluster) for query in queries if getattr(query, "source_cluster", None)})
    windows = sorted({(origin["summary_begin_time"], origin["summary_end_time"]) for origin in origins
                      if origin.get("summary_begin_time") and origin.get("summary_end_time")})
    cards = [
        ("时间范围", time_range), ("筛选库", selection.get("schemas") or "未指定"),
        ("SQL 类型", [str(s).upper() for s in selection.get("statement_types", [])] or "未记录"),
        ("筛选方式", method), ("排名指标", metrics or "未记录"), ("来源集群", clusters or "未记录"),
        ("源端版本", context.get("version")),
        ("升级前版本", _versions(snapshots, "old")), ("升级后版本", _versions(snapshots, "new")),
    ]
    if windows:
        window_text = "；".join(f"{_display_time(start)} 至 {_display_time(stop)}" for start, stop in windows[:3])
        if len(windows) > 3:
            window_text += f"；另有 {len(windows) - 3} 个窗口，详见各 SQL 的来源"
        cards.insert(1, ("实际摘要窗口", window_text))
    notes = []
    if selection.get("window_semantics") == "overlapping_cumulative_windows":
        notes.append("时间范围用于筛选与其相交的 Statement Summary 窗口；指标是这些窗口的累计值，不是区间内精确流量。")
    if context.get("stats_time_semantics") == "export_time_not_business_window":
        notes.append("表结构和统计信息取导出时的状态，不代表所选历史时间范围内的快照。")
    if selection.get("coverage") == "visible_summary_only":
        notes.append("SQL 仅来自采集时仍可见的 Statement Summary，不能代表全部业务 SQL。")
    input_html = ('<dl class="overview-grid">' + "".join(_overview_card(k, v) for k, v in cards) + '</dl>'
                  + "".join(f'<p class="overview-note">{escaped(note)}</p>' for note in notes)
                  + _data_details("查看完整的筛选和环境字段", {"selection": selection, "context": context}))

    digest_count = summary.get("digest_count")
    if kind == "explicit_samples" and str(digest_count) == "0":
        digest_count = "未提供源摘要标识"
    summary_cards = [("选中摘要", digest_count), ("SQL 样例", summary.get("selected_samples")),
                     ("可执行样例", summary.get("executable_samples")), ("双端采集成功", summary.get("both_captured")),
                     ("完整比较", summary.get("full_comparisons")), ("完整比较率", _percentage(summary.get("complete_comparison_rate"))),
                     ("缺少样例", summary.get("needs_sample")),
                     ("归并后的计划事实", summary.get("plan_findings")),
                     ("辅助指标变化", summary.get("auxiliary_findings")),
                     ("已确认字段事件", summary.get("confirmed_events")),
                     ("未确认观察事件", summary.get("unconfirmed_events"))]
    denominator = ("完整比较率以全部选中样例为分母，包含缺少样例的记录。"
                   if summary.get("coverage_denominator") == "selected_samples_including_unresolved"
                   else "完整比较率的分母口径：" + _overview_value(summary.get("coverage_denominator")) + "。")
    coverage = summary.get("business_coverage")
    coverage_note = "业务 SQL 总体覆盖率未知。" if coverage == "unknown" else "业务 SQL 总体覆盖率：" + _overview_value(coverage) + "。"
    summary_html = ('<dl class="overview-grid overview-counts">' + "".join(_overview_card(k, v) for k, v in summary_cards)
                    + f'</dl><p class="overview-note">{escaped(denominator)} {escaped(coverage_note)}</p>'
                    + '<p class="overview-note">缺少样例属于无法比对的原因，不能与结果数量相加。无法比对的观察不计入已确认事件和计划事实。</p>'
                    + f'<div class="fingerprint"><span>比较指纹</span><code>{escaped(result.fingerprint or "未记录")}</code>'
                    + '<small>用于标识比较输入和设置，方便追溯及恢复。</small></div>'
                    + _data_details("查看完整的摘要字段", summary))

    source_issues = collection.get("issues") or []
    issue_html = (f'<p class="overview-lead">比较问题 {len(issues)} 条记录；同一问题可能分别记录在多条 SQL 或两端。</p>'
                  + _issue_groups(issues, query_positions, query_by_id))
    if source_issues:
        issue_html += (f'<h3 class="overview-subheading">源端采集提醒 · {len(source_issues)} 条记录</h3>'
                       + _issue_groups(source_issues, query_positions, query_by_id))
    panels = [("采集范围与环境", "时间范围、筛选方式和版本", input_html),
              ("比较摘要与追溯标识", "样例数量、覆盖率和指纹", summary_html),
              ("问题与覆盖限制", "问题原因和受影响的 SQL", issue_html)]
    return '<div class="report-details">' + "".join(
        f'<details class="report-panel"><summary><span><strong>{title}</strong><small>{subtitle}</small></span></summary>'
        f'<div class="report-panel-body">{body}</div></details>' for title, subtitle, body in panels) + '</div>'


STYLES = '''
:root{color-scheme:light;--ink:#242426;--muted:#76767d;--line:#e7e7eb;--blue:#0067c5;--surface:#fff}
*{box-sizing:border-box}body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;margin:0;background:#f5f5f7;color:var(--ink);-webkit-font-smoothing:antialiased}
main{max-width:1680px;margin:auto;padding:28px 32px 48px}h1,h2,h3,p{margin-top:0}h1{font-size:29px;line-height:1.2;letter-spacing:-.8px;font-weight:650;margin-bottom:0}h2{font-size:20px;font-weight:600;margin-bottom:0}h3{font-size:14px;font-weight:600;margin-bottom:0}
header{margin-bottom:20px}.heading,.card-heading,.plan-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px}.eyebrow{font-size:11px;letter-spacing:2px;font-weight:650;color:var(--muted);margin-bottom:5px}.subtitle{font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;color:var(--muted);margin:9px 0 18px}.version-arrow{padding:0 12px;color:#a6a6ad}
button,input,select,textarea{font:inherit}button{cursor:pointer}button:disabled{opacity:.35;cursor:default}input[type="checkbox"]{accent-color:var(--blue)}button,input,select{transition:border-color .15s,background .15s,color .15s}
.run-status,.badge{font-size:11px;padding:5px 10px;border-radius:20px;font-weight:500;white-space:nowrap}.run-status{background:#eaeaee;color:#696971}.badge.changed{color:#906013;background:#fbf1de}.badge.same{color:#33714e;background:#edf5ef}.badge.incomparable,.badge.not_run{color:#a24240;background:#fcf0ee}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{background:white;border:1px solid #e8e8ed;border-radius:12px;text-align:left;padding:14px 20px;color:var(--ink)}.metric b{display:block;font-size:32px;line-height:1.2;font-weight:600;letter-spacing:-1px;font-variant-numeric:tabular-nums}.metric small{display:block;font-size:12px;margin-top:5px;color:var(--muted)}.metric:hover{border-color:#b9bac1}.metric[aria-pressed="true"]{background:#27272a;border-color:#27272a;color:white}.metric[aria-pressed="true"] small{color:#e1e1e5}.metric[data-filter="same"]:not([aria-pressed="true"]) b{color:#43795a}.metric[data-filter="changed"]:not([aria-pressed="true"]) b{color:#a57828}.metric[data-filter="unavailable"]:not([aria-pressed="true"]) b{color:#b36460}
.report-footnote{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:11px}.coverage{font-size:11px;color:var(--muted);margin:0}.report-context{margin:0 0 0 auto;font-size:12px}.report-context>summary{color:var(--muted);font-size:11px}.report-context[open]{flex-basis:100%;margin-left:0}.report-context[open]>summary{text-align:right}.report-context-body{margin-top:12px;padding:20px 24px;background:#fff;border:1px solid var(--line);border-radius:12px}.report-context-body>.coverage{margin:12px 0}.count-equation{font-size:12px;color:#62626a;margin-bottom:8px}
article{margin:0;padding:22px 26px 26px;scroll-margin-top:16px;min-width:0}.card-heading>div{min-width:0}.query-number{font-weight:500;color:#888890;margin-left:3px;font-variant-numeric:tabular-nums}.query-schema{font:11px/1.5 ui-monospace,SFMono-Regular,monospace;color:var(--muted);margin:5px 0 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.id{font:11px/1.6 ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere;margin:8px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f7f7f9;padding:14px 16px;border-radius:8px;max-height:400px;overflow:auto;margin:12px 0}.sql{font-size:13px;color:#353840;background:#f6f6f8;border:1px solid #eeeeF1;margin:18px 0;max-height:155px;tab-size:2}.parameters{margin:-8px 0 14px}.parameters>summary{font-size:11px}
details{margin:9px 0}summary{cursor:pointer;color:#676771;font-size:12px}summary:hover{color:var(--blue)}.muted{color:var(--muted)}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.finding-review{border:1px solid #e8e8ed;border-radius:8px;padding:11px 14px;margin:14px 0 20px;background:#fcfcfd}.finding-review>summary{display:flex;align-items:baseline;gap:12px;list-style:none;font-size:12px;flex-wrap:wrap}.finding-review>summary::-webkit-details-marker{display:none}.finding-review>summary strong{color:#4a4a52;font-weight:600}.finding-glance{color:#75757e}.aux-count{color:#5d7694;border-left:1px solid #dddde3;padding-left:12px;font-size:11px}.disclosure-action{margin-left:auto;color:var(--blue);font-size:11px}.finding-review[open]>summary{padding-bottom:12px;border-bottom:1px solid var(--line)}.finding-review[open] .disclosure-action{font-size:0}.finding-review[open] .disclosure-action:after{content:"收起";font-size:11px}.finding-section{margin:18px 0}.finding-section>h3{font-size:13px}.finding{border:1px solid var(--line);background:white;border-radius:8px;padding:14px 16px;margin-top:12px;scroll-margin-top:16px}.auxiliary .finding{border-color:#e1e8f0}
.fact-pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:12px 0}.fact-pair>div{min-width:0;padding:9px 12px;background:#fafafb;border-radius:6px}.fact-pair small{font-size:11px;color:var(--muted)}.fact-node{margin:7px 0;overflow-wrap:anywhere}.fact-node a{color:#526b8e}.fact-node span{display:block;color:var(--muted);font-size:11px}.fact-node .estimate-value{font-size:17px;color:#5a7699}.rule-hits li{font-size:12px;margin:6px 0}.rule-hits code{color:var(--muted)}.rule-evidence a{color:var(--blue)}
.unavailable-reason{padding:12px 14px;background:#fcf6f5;border-radius:8px;margin:14px 0 20px;border-left:3px solid #cf9189}.unavailable-reason>div{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;font-size:12px}.unavailable-reason strong{font-weight:600;color:#99534d}.unavailable-reason span{color:#85655f}.unavailable-reason p,.unavailable-reason li{font-size:12px}.unavailable-reason code{color:#a3736f;font-size:10px}.unavailable-reason details{margin:6px 0 0}.unavailable-reason summary{font-size:11px}
.plan-toolbar{margin:20px 0 10px}.plan-actions{display:flex;gap:14px;align-items:center}.plan-actions label{font-size:11px;color:var(--muted);cursor:pointer}.plan-actions input{vertical-align:-2px;margin-right:4px}.plan-meta{display:flex;justify-content:space-between;gap:12px;align-items:center;min-height:16px;margin-bottom:9px}.legend{font-size:10px;color:var(--muted);display:flex;gap:7px}.legend mark{padding:1px 6px;text-decoration:none}.copy-status{font-size:11px;color:#43795a}.copy-plan{font-size:11px;padding:4px 9px;border:1px solid #dedee4;border-radius:6px;background:#fff;color:#64646e;white-space:nowrap}.copy-plan:hover{background:#f3f3f6;border-color:#b8bac3}.copy-manual{width:100%;height:180px;font:12px/1.6 ui-monospace,monospace}
.plan-scroll{overflow:auto;border:1px solid #e7e7ec;border-radius:9px;max-height:65vh;overscroll-behavior:contain;position:relative;scrollbar-gutter:stable}.plan-table{width:100%;border-collapse:collapse;table-layout:fixed}.plan-table th{width:50%;background:#f6f6f8;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:2;box-shadow:0 1px 0 var(--line)}.plan-column-heading{display:flex;align-items:center;gap:10px}.plan-column-heading .copy-plan{margin-left:auto}.side-label{font-size:12px;font-weight:600;color:#4c4c55}.version{font:11px/1.7 ui-monospace,monospace;font-weight:400;color:var(--muted)}.plan-table th+th,.plan-table td+td{border-left:1px solid #dddde4}.plan-cell{vertical-align:top;padding:0 12px;border-top:1px solid #f0f0f3;background:white}.plan-table .old.changed-cell{background:#fffaf8}.plan-table .new.changed-cell{background:#f8fcf9}
.node-block{position:relative;margin-left:calc(var(--depth) * 24px);border-left:2px solid #bdc4cf;padding:9px 0 9px 11px;min-width:170px}.node-block:before{content:"";position:absolute;left:-16px;top:21px;width:16px;border-top:2px solid #bdc4cf}.node-block[data-depth="0"]{border-left:3px solid #7d8da4}.node-block[data-depth="0"]:before{display:none}.node-depth{font:9px/1.6 ui-monospace,monospace;color:#97a0ac}.node-line{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.operator{font:600 13px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre;max-width:100%;overflow-x:auto;color:#374252}.tree-prefix{color:#8d9db3;font-weight:400;margin-right:4px}.role{font-size:10px;color:#8290a2;font-weight:400}.node-task{font:11px/1.6 ui-monospace,monospace;color:#6e7888}.node-estimate{font:12px/1.6 ui-monospace,monospace;margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}.node-estimate small,.node-unknown small{font:9px/1.6 system-ui;color:#9097a2;margin-right:5px}.node-object{font-size:11px;margin-top:3px;color:#687487;overflow-wrap:anywhere}.node-events:empty{display:none}.node-change{display:inline-block;color:#8a7557;font:9px/1.6 ui-monospace,monospace;margin:4px 4px 0 0;padding:0 4px;text-decoration:none;border:1px solid #e8dfd2;border-radius:3px}.node-change:hover{background:#fbf1de}.node-details{margin:3px 0 0}.node-details summary{font-size:10px;color:#8992a1}.node-info,.node-unknown{font:11px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:#687385;padding-top:4px}.plan-gap{background:#fafafb;text-align:center;color:#c9cbd1;vertical-align:middle}.no-plan{padding:32px;text-align:center;font-size:12px;color:var(--muted)}
mark{color:inherit;border-radius:3px;padding:1px 2px}.removed{background:#f8dcd6;text-decoration:underline;text-decoration-color:#c9877c;text-underline-offset:3px}.added{background:#d4eddb;text-decoration:underline;text-decoration-color:#73a487;text-underline-offset:3px}
.technical-details{margin:18px 0 0;border-top:1px solid var(--line);padding-top:13px}.technical-details>summary{font-size:11px;color:#91919a}.field-records{margin:14px 0}.diff-event{scroll-margin-top:16px}.diff-event>summary{font-size:12px}.diff-values{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}.diff-value{min-width:0;font:12px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto;padding:10px;border-radius:6px}.diff-value small{display:block;font:10px system-ui;margin-bottom:5px;color:var(--muted)}.diff-value.old{background:#fdf5f2}.diff-value.new{background:#f1f9f3}.raw-plans{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.report-details{display:grid;gap:8px;margin-top:16px}.report-panel{margin:0;min-width:0;border:1px solid var(--line);border-radius:8px;background:#fff}.report-panel>summary{display:flex;align-items:center;gap:10px;padding:12px 14px;list-style:none;color:#575761}.report-panel>summary::-webkit-details-marker{display:none}.report-panel>summary:before{content:"▸";color:#9595a0}.report-panel[open]>summary:before{content:"▾"}.report-panel>summary span{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}.report-panel>summary strong{font-size:12px;font-weight:600}.report-panel>summary small{font-size:11px;color:var(--muted)}.report-panel[open]>summary{border-bottom:1px solid var(--line)}.report-panel-body{padding:16px;min-width:0}.method-panel{margin-top:8px}.method-steps{padding-left:20px}.method-steps li{margin:10px 0;font-size:12px;color:#686873}
.overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:10px;margin:0}.overview-card{padding:10px 12px;border-radius:7px;background:#f7f7f9;min-width:0}.overview-card dt{font-size:11px;color:var(--muted)}.overview-card dd{font-size:13px;font-weight:500;margin:3px 0 0;overflow-wrap:anywhere}.overview-counts dd{font-size:19px}.overview-note,.overview-lead,.overview-empty{font-size:11px;color:var(--muted);margin:10px 0 0}.fingerprint{margin-top:14px;padding:12px 14px;background:#f7f7f9;border-radius:7px}.fingerprint span,.fingerprint small{display:block;font-size:11px;color:var(--muted)}.fingerprint code{display:block;overflow-wrap:anywhere;margin:3px 0;font-size:12px}.overview-subheading{font-size:12px;margin:20px 0 8px;color:#64646f}
.overview-issue{border:1px solid #eddfc8;border-radius:8px;background:#fffcf7;margin-top:10px;padding:12px 14px}.overview-issue.error{border-color:#efdad4;background:#fefaf9}.issue-heading{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}.issue-heading strong{font-size:12px;font-weight:600;color:#6e6255}.issue-heading span,.issue-code{font-size:10px;color:var(--muted)}.overview-issue>p{margin:6px 0;font-size:12px}.issue-help{color:#7b7167}.issue-location{overflow-wrap:anywhere}.issue-code{margin-top:10px!important}.issue-queries{display:flex;gap:6px;flex-wrap:wrap;margin:9px 0}.query-link{display:inline-block;padding:2px 8px;border:1px solid #dce6f2;border-radius:5px;background:#f7faff;color:#597599;font-size:11px;text-decoration:none;overflow-wrap:anywhere}.issue-example span{display:block;font-size:11px;color:var(--muted)}.issue-example code{display:block;white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:8px;border-radius:5px}
.overview-table-wrap{overflow-x:auto;margin:10px 0}.overview-table{width:100%;border-collapse:collapse;font-size:12px;background:#fff}.overview-table th,.overview-table td{padding:7px 9px;border:1px solid var(--line);text-align:left;overflow-wrap:anywhere}.overview-table th{color:#73737c;font-weight:500}.overview-table th code{display:block;font-size:10px;font-weight:400;color:var(--muted)}.data-extra{margin-top:12px;border-top:1px solid var(--line);padding-top:8px}.data-extra>summary{font-weight:500}.data-branch{margin:5px 0;min-width:0}.data-tree{margin:6px 0;padding-left:10px;border-left:2px solid var(--line)}.data-tree>div{display:grid;grid-template-columns:minmax(100px,180px) minmax(0,1fr);gap:8px;padding:4px 0}.data-tree dt,.data-items>li>span{font-size:11px;color:var(--muted);overflow-wrap:anywhere}.data-tree dd{margin:0;min-width:0;font-size:12px;overflow-wrap:anywhere}.data-items{margin:6px 0;padding-left:28px}.data-items>li{padding:3px 0}.data-items>li>span{margin-right:8px}.data-empty{color:var(--muted)}
.grouping{border-top:1px solid var(--line);padding:12px 0 0;margin:12px 0 0}.grouping>summary{font-size:12px}.grouping>p{font-size:11px;color:var(--muted)}.similar-group{border-top:1px solid var(--line);padding-top:8px}.similar-group>summary{font-size:12px}.similar-group button{padding:5px 10px;border:1px solid #d1d9e4;border-radius:6px;background:#fff;color:#5175a3;margin:8px 0}.issue-note{background:#fcf5e8;color:#8f773e;padding:10px 14px;border-radius:6px;font-size:12px}.empty{padding:80px 24px;text-align:center;color:var(--muted);font-size:13px}
[hidden]{display:none!important}:focus-visible{outline:3px solid #9ac2e8;outline-offset:3px}a{color:var(--blue)}
@media(max-width:1000px){main{padding:20px 16px}article{padding:20px}.metric{padding:12px 16px}}
@media(max-width:600px){main{padding:16px 10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric b{font-size:27px}h1{font-size:25px}.fact-pair,.raw-plans,.diff-values{grid-template-columns:1fr}.data-tree>div{grid-template-columns:1fr;gap:0}.report-panel>summary span{display:block}.report-panel>summary small{display:block}.report-context-body{padding:14px}.plan-toolbar{align-items:flex-start;gap:8px}.plan-actions{gap:8px}}
'''


def render_reports(root, result, queries, snapshots, irs, selection, context):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["query_id", "schema", "compare_status", "sql_text", "event", "field", "old", "new", "requires_review", "parameters"])
    diffs_by_query = {}
    for event in result.diffs:
        diffs_by_query.setdefault(event.query_id, []).append(event)
    query_by_id = {q.query_id: q for q in queries}
    cards = []
    records = []
    for index, item in enumerate(result.items, 1):
        q = query_by_id[item["query_id"]]
        events = diffs_by_query.get(q.query_id, [])
        for event in events or [None]:
            writer.writerow([csv_safe(v) for v in (q.query_id, q.schema, item["compare_status"], q.sql_text,
                event.type if event else "", event.field if event else "", canonical(event.old) if event else "",
                canonical(event.new) if event else "", event.requires_review if event else item.get("requires_review", False),
                canonical(q.parameters) if q.parameters is not None else "")])
        anchor = f"query-{index}"
        sides = []
        for side in ("old", "new"):
            snapshot = snapshots.get((q.query_id, side))
            sides.append(f'<details><summary>{side} · 上下文、原始计划与警告</summary>'
                         f'<pre>{escaped(primitive(snapshot) if snapshot else {})}</pre></details>')
        diff_parts = []
        for n, event in enumerate(events, 1):
            old, new = highlighted_pair(event.old, event.new)
            links = " ".join(f'<a href="#{anchor}-{side}-{escaped(node)}">定位 {side}</a>'
                             for side, node in (("old", event.old_node), ("new", event.new_node)) if node is not None)
            diff_parts.append(f'<details class="diff-event" id="{anchor}-diff-{n}">'
                              f'<summary>字段 {n} · {escaped(EVENT_LABELS.get(event.type, event.type))} · {escaped(event.field)}</summary>'
                              f'<div class="diff-values"><div class="diff-value old"><small>旧值 · old</small>{old}</div>'
                              f'<div class="diff-value new"><small>新值 · new</small>{new}</div></div>'
                              f'<details><summary>差异证据</summary>{links}<pre>{escaped(primitive(event))}</pre></details></details>')
        record_label = "底层字段记录" if item["compare_status"] == "changed" else "未确认的字段观察（不计入差异）"
        diff_html = (f'<details class="field-records"><summary>{record_label} · {len(events)} 条</summary>'
                     f'{"".join(diff_parts)}</details>') if events else ''
        status = item["compare_status"]
        needs_attention = status != "same" or item.get("requires_review", False)
        note = unavailable_reason(item, ISSUE_LABELS)
        facts_html = findings_panel(item, events, anchor)
        category = status if status in {"same", "changed"} else "unavailable"
        cards.append(f'<article id="{anchor}" data-status="{escaped(status)}" data-attention="{str(needs_attention).lower()}" '
                     f'data-category="{category}" data-needs-sample="{str(bool(item.get("needs_sample"))).lower()}">'
                     f'<div class="card-heading"><div><h2>SQL <span class="query-number">#{index:03d}</span></h2>'
                     f'<p class="query-schema" title="{escaped(q.schema or "全限定 SQL")}">{escaped(q.schema or "全限定 SQL")}</p></div>'
                     f'<span class="badge {escaped(status)}">{escaped(STATUS_LABELS[status])}</span></div>'
                     f'<pre class="sql">{escaped(q.sql_text or "缺少可执行样例")}</pre>'
                     + (f'<details class="parameters"><summary>参数 · {len(q.parameters)} 个</summary><p class="overview-note">参数样例用于重新优化，不复现计划缓存。</p><pre>{escaped(q.parameters)}</pre></details>' if q.parameters is not None else '')
                     +
                     f'{note}{facts_html}<div class="plan-toolbar"><h3>执行计划</h3>'
                     '<div class="plan-actions"><label><input class="expand-plan" type="checkbox"> 展开详情</label>'
                     '<button type="button" class="copy-plan" data-copy-plan="both">复制两侧</button></div></div>'
                     '<div class="plan-meta"><div class="legend"><span><mark class="removed">旧值</mark></span><span><mark class="added">新值</mark></span></div>'
                     '<span class="copy-status" role="status"></span></div>'
                     f'{plan_table(q.query_id, irs, snapshots, events, anchor)}'
                     '<details class="technical-details"><summary>原始记录与来源</summary>'
                     f'<p class="id">{escaped(q.query_id)}</p>{diff_html}<div class="raw-plans">{"".join(sides)}</div>'
                     f'<details><summary>排名、来源、覆盖与问题</summary><pre>{escaped({"metrics": q.metrics, "origins": q.sample_origins, "item": item})}</pre></details></details></article>')
        group_key, group_title = plan_group(irs, q.query_id, status)
        records.append(dict(id=anchor, number=index, schema=q.schema, status=status, category=category,
                            attention=needs_attention, needsSample=bool(item.get("needs_sample")),
                            planGroup=group_key, planTitle=group_title,
                            preview=" ".join((q.sql_text or "缺少可执行样例").split())[:100],
                            search=(q.schema + " " + q.query_id + " " + (q.sql_text or "") + " " +
                                    " ".join(tree_text(irs.get((q.query_id, side))) for side in ("old", "new"))).lower(),
                            oldPlan=plan_copy_text(snapshots.get((q.query_id, "old")), irs.get((q.query_id, "old"))),
                            newPlan=plan_copy_text(snapshots.get((q.query_id, "new")), irs.get((q.query_id, "new"))),
                            html=cards[-1]))
    report = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>PCC 执行计划比较报告</title><style>''' + STYLES + NAV_STYLES + '</style></head><body><main><header><div class="heading"><div><p class="eyebrow">PCC</p><h1>执行计划比较</h1></div>'
    status_label = {"completed": "完成", "partial": "部分完成", "blocked": "前置条件不足", "interrupted": "已中断"}[result.run_status]
    report += f'<span class="run-status">{status_label}</span></div>'
    report += f'<p class="subtitle">{escaped(_versions(snapshots, "old"))}<span class="version-arrow">→</span>{escaped(_versions(snapshots, "new"))}</p>'
    unavailable = sum(i["compare_status"] not in {"same", "changed"} for i in result.items)
    metrics = [("", "全部样例", result.summary.get("selected_samples", "0")),
               ("same", "执行计划一致", result.summary.get("same", "0")),
               ("changed", "存在差异", result.summary.get("changed", "0")), ("unavailable", "无法比对", unavailable)]
    report += '<div class="metrics" role="group" aria-label="按比较结果筛选">' + "".join(
        f'<button type="button" class="metric" data-filter="{key}" aria-pressed="{str(key == "").lower()}">'
        f'<b>{escaped(str(count))}</b><small>{label}</small></button>' for key, label, count in metrics) + '</div>'
    rate = result.summary.get("complete_comparison_rate")
    rate_text = f"{Decimal(rate) * 100:.2f}%" if rate is not None else "N/A"
    report += f'<div class="report-footnote"><p class="coverage">已完整比较 {escaped(result.summary.get("full_comparisons", "0"))} / {len(result.items)} 条 · {rate_text}</p>'
    report += '<details class="report-context"><summary>报告说明</summary><div class="report-context-body">'
    report += (f'<p class="count-equation">{len(result.items)} 条样例 = '
               f'{escaped(result.summary.get("same", "0"))} 条一致 + {escaped(result.summary.get("changed", "0"))} 条存在差异 + '
               f'{unavailable} 条无法比对。无法比对中：未运行 {escaped(result.summary.get("not_run", "0"))} 条，'
               f'未完成完整比较 {escaped(result.summary.get("incomparable", "0"))} 条；缺少样例是其中的原因标记。</p>')
    report += '<p class="coverage">比较声明上下文下的普通 EXPLAIN；计划或估算值变化不代表实际性能变差。生产复现可信度尚未验证。</p>'
    if context.get("validation_note"):
        report += f'<p class="issue-note">{escaped(context["validation_note"])}</p>'
    report += report_overview(selection, context, result, queries, snapshots) + comparison_method()
    report += grouping_panel(result.items, queries) + '</div></details></div></header>'
    report += navigation_markup(records)
    report += '</main><script>' + SCRIPT + '</script></body></html>'
    write_bytes(root / "report.html", report.encode())
    write_bytes(root / "differences.csv", buffer.getvalue().encode("utf-8-sig"))
