import csv
import html
import io
from decimal import Decimal
from difflib import SequenceMatcher

from pcc_tool.artifacts.store import write_bytes
from pcc_tool.models import canonical, primitive

STATUS_LABELS = {"same": "一致", "changed": "变化", "incomparable": "不可完整比较", "not_run": "未运行"}
EVENT_LABELS = {"node_added": "新增算子", "node_removed": "移除算子", "operator_changed": "算子变化",
                "access_path_changed": "访问路径变化", "join_changed": "Join / 角色变化", "child_order_changed": "子节点顺序变化",
                "task_or_engine_changed": "任务或引擎变化", "predicate_or_attribute_changed": "谓词或属性变化",
                "estimate_changed": "估算行数变化", "unknown_field_changed": "未知字段变化", "ambiguous_match": "匹配有歧义"}


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
    fields = {"operator": ("operator",), "access": ("access",), "task/engine": ("task", "engine"),
              "join/role": ("operator", "role", "attributes"), "operator_info": ("attributes",),
              "estRows": ("estimate",), "unknown": ("unknown",)}
    for index, event in enumerate(events, 1):
        for side in sides:
            node_id = getattr(event, side + "_node")
            if node_id in nodes[side]:
                labels[side].setdefault(node_id, []).append((index, EVENT_LABELS.get(event.type, event.type)))
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
            return '<td class="plan-gap"><span>—</span><small>此侧无对应行</small></td>'
        node, prefix = row
        changes = marks[side].get(node.node_id, {})
        tags = labels[side].get(node.node_id, [])
        def value(field, fallback="—"):
            raw = getattr(node, field)
            return changes.get(field, escaped(raw if raw is not None and raw != "" else fallback))
        badges = "".join(f'<a class="node-change" href="#{anchor}-diff-{i}">差异 {i} · {escaped(label)}</a>' for i, label in tags)
        role = f' <span class="role">{value("role")}</span>' if node.role or "role" in changes else ""
        unknown = f'<div class="node-unknown"><small>其他字段</small> {value("unknown")}</div>' if node.unknown or "unknown" in changes else ""
        # Keep unchanged long expressions collapsed; changed fields are visible immediately.
        info = ""
        if node.attributes or "attributes" in changes or unknown:
            expanded = ' open' if changes.get("attributes") or unknown or tags else ''
            info = (f'<details class="node-details"{expanded}><summary>算子详情</summary>'
                    f'<div class="node-info">{value("attributes")}</div>{unknown}</details>')
        engine = f' · {value("engine")}' if "engine" in changes else ''
        return (f'<td id="{anchor}-{side}-{escaped(node.node_id)}" class="plan-cell {side}{" changed-cell" if tags else ""}">'
                f'<div class="node-line"><code class="operator"><span class="tree-prefix">{escaped(prefix)}</span>{value("operator")}{role}</code>'
                f'<span class="node-task">{value("task")}{engine}</span>'
                f'<span class="node-estimate"><small>estRows</small> {value("estimate")}</span></div>'
                f'<div class="node-object"><small>访问对象</small> <code>{value("access")}</code>{badges}</div>{info}</td>')

    heads = []
    for side, label in (("old", "升级前"), ("new", "升级后")):
        snap = snapshots.get((query_id, side))
        heads.append(f'<th scope="col"><span class="side-label">{label} · {side}</span>'
                     f'<span class="version">{escaped(snap.version if snap else "未采集")}</span></th>')
    pairs = aligned_rows(rows["old"], rows["new"], events)
    body = "".join('<tr>' + cell(left, "old") + cell(right, "new") + '</tr>' for left, right in pairs)
    if not pairs:
        body = '<tr><td colspan="2" class="no-plan">没有可用计划，请查看本条 SQL 的问题说明。</td></tr>'
    return '<div class="plan-scroll"><table class="plan-table"><thead><tr>' + "".join(heads) + '</tr></thead><tbody>' + body + '</tbody></table></div>'


STYLES = '''
:root{color-scheme:light;--ink:#172438;--muted:#627287;--line:#dce4ed;--blue:#2459a6}
*{box-sizing:border-box}body{font:14px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif;margin:0;background:#f3f6fa;color:var(--ink)}
main{max-width:1640px;margin:auto;padding:28px 32px 60px}h1{font-size:28px;letter-spacing:-.5px;margin:0}h2{font-size:17px;margin:0}h3{font-size:15px;margin:0}
header,article{background:#fff;border:1px solid var(--line);border-radius:12px;padding:24px;margin-bottom:20px}
.heading,.card-heading,.plan-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.eyebrow{font-size:11px;font-weight:700;letter-spacing:2px;color:var(--blue);margin:0 0 4px}
.run-status,.badge{font-size:12px;padding:4px 10px;border-radius:6px;font-weight:600;background:#edf2f8}.run-status{color:#855310;background:#fff4da}
.subtitle,.muted{color:var(--muted)}.subtitle{margin:12px 0 18px}.metrics{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:10px;margin:18px 0}
button,input,select{font:inherit}button{cursor:pointer}.metric{background:#f8fafc;border:1px solid var(--line);border-radius:9px;text-align:left;padding:13px 16px;color:var(--ink);transition:background .15s}
.metric b{display:block;font-size:27px;line-height:1.3;margin-bottom:5px}.metric small{font-size:12px;color:var(--muted)}.metric:hover{background:#edf3fc;border-color:#95b4dc}
.metric[aria-pressed="true"]{background:#edf4ff;border-color:var(--blue);box-shadow:inset 0 0 0 1px var(--blue)}.metric[aria-pressed="true"] small{color:var(--blue);font-weight:700}
.metric[data-filter="attention"] b,.metric[data-filter="changed"] b{color:#a45b05}.metric[data-filter="same"] b{color:#177457}.coverage{font-size:12px;color:var(--muted)}
details{margin:8px 0}summary{cursor:pointer;color:#455b76;font-size:12px}summary:hover{color:var(--blue)}.report-details{display:flex;gap:14px;flex-wrap:wrap}.report-details details{flex:1;min-width:210px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f6f8fb;padding:14px;border-radius:6px;max-height:450px;overflow:auto;margin:10px 0}
.filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:20px 0 14px}.filters input,.filters select{padding:9px 12px;border:1px solid #c7d2df;background:white;border-radius:7px;color:var(--ink)}.filters input{flex:1;min-width:220px}.filters select{min-width:180px}#visible-count{font-size:12px;color:var(--muted)}
article[data-status="changed"]{border-top:3px solid #cc831e}article[data-status="incomparable"],article[data-status="not_run"]{border-top:3px solid #b84b50}
.badge.changed{color:#945200;background:#fff0d6}.badge.same{color:#17684c;background:#e5f5ed}.badge.incomparable,.badge.not_run{color:#a23542;background:#fdecef}.card-heading h2{overflow-wrap:anywhere}.query-number{color:#7c8da3;margin-right:12px;font-weight:500}
.id{font:11px/1.6 ui-monospace,monospace;color:#79879a;overflow-wrap:anywhere;margin:8px 0}.sql{font-size:13px;color:#253c58;border:1px solid #e5ebf2;margin:14px 0 18px}
.diff-list{border:1px solid #efd6ab;border-radius:8px;padding:12px 16px;margin:16px 0;background:#fffcf6}.diff-list h3{color:#85500d}.diff-event{scroll-margin-top:16px}.diff-event>summary{font-weight:600;color:#85500d}.diff-values{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}.diff-value{min-width:0;font:12px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto;padding:10px;border-radius:5px}.diff-value small{display:block;font:11px system-ui;margin-bottom:5px}.diff-value.old{background:#fff1ef}.diff-value.new{background:#edf9f2}
mark{color:inherit;border-radius:3px;padding:1px 2px}.removed{background:#ffcfc9;text-decoration:underline;text-decoration-color:#c54b40;text-underline-offset:3px}.added{background:#bdeed1;text-decoration:underline;text-decoration-color:#268153;text-underline-offset:3px}
.plan-toolbar{margin:18px 0 10px}.legend{font-size:11px;color:var(--muted)}.legend span{margin-right:10px}.plan-toolbar label{font-size:12px;cursor:pointer}.plan-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}.plan-table{width:100%;min-width:880px;border-collapse:collapse;table-layout:fixed}.plan-table th{width:50%;background:#f0f4f9;text-align:left;padding:12px 15px;border-bottom:1px solid var(--line)}.plan-table th+th,.plan-cell+.plan-cell,.plan-gap+.plan-cell,.plan-cell+.plan-gap{border-left:2px solid var(--line)}
.side-label{font-size:13px;font-weight:700}.version{float:right;color:var(--muted);font:12px/1.8 ui-monospace,monospace}.plan-cell{vertical-align:top;padding:11px 14px;border-top:1px solid #e7edf3;background:white}.plan-table tr:nth-child(even) .plan-cell{background:#fafbfd}.plan-table .plan-cell.old.changed-cell{background:#fff2ef}.plan-table .plan-cell.new.changed-cell{background:#edf9f2}
.node-line{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}.operator{font:600 13px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre;max-width:100%;overflow-x:auto}.tree-prefix{color:#94a2b5;font-weight:400}.role{font-size:11px;color:#5f7290;font-weight:400}.node-task{font:11px/1.6 ui-monospace,monospace;color:#5a6c84;background:#edf2f8;border-radius:4px;padding:1px 5px}.node-estimate{font:12px/1.6 ui-monospace,monospace;margin-left:auto;white-space:nowrap}.node-estimate small,.node-object small,.node-unknown small{font:10px/1.6 system-ui;color:var(--muted);margin-right:5px}.node-object{font-size:11px;margin-top:4px;color:#455b76;overflow-wrap:anywhere}.node-change{color:#8d5409;font:10px/1.6 system-ui;margin-left:12px;text-decoration:none;border-bottom:1px dotted #b78135}.node-details{margin:5px 0 0}.node-details summary{font-size:10px;color:#6b7d94}.node-info,.node-unknown{font:11px/1.8 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;color:#394e68;padding:6px 0 0}
.plan-gap{background:repeating-linear-gradient(-45deg,#f7f9fc,#f7f9fc 6px,#f1f4f8 6px,#f1f4f8 7px);text-align:center;color:#95a2b4;border-top:1px solid var(--line)}.plan-gap span{display:block}.plan-gap small{font-size:10px}.no-plan{padding:28px;text-align:center;color:var(--muted)}.raw-plans{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:10px}.issue-note{background:#fff4e3;color:#8a591e;padding:10px 14px;border-radius:6px;font-size:12px}.empty{border:1px dashed #b8c9dc;padding:32px;text-align:center;border-radius:10px;background:#fff;color:var(--muted)}
[hidden]{display:none!important}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #8ab6ed;outline-offset:3px}
@media(max-width:1000px){main{padding:16px}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}header,article{padding:18px}}
@media(max-width:600px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.diff-values,.raw-plans{grid-template-columns:1fr}h1{font-size:23px}.filters input{min-width:100%}}
'''

SCRIPT = '''
const search=document.getElementById('search'), status=document.getElementById('status');
const cards=Array.from(document.querySelectorAll('article[data-status]'));
const metrics=Array.from(document.querySelectorAll('button[data-filter]'));
function filter(){
  const query=search.value.toLowerCase(), selected=status.value;
  let visible=0;
  cards.forEach(card=>{
    const matches=selected==='attention'?card.dataset.attention==='true':selected==='needs_sample'?card.dataset.needsSample==='true':!selected||card.dataset.status===selected;
    card.hidden=!(matches&&card.textContent.toLowerCase().includes(query));
    if(!card.hidden) visible++;
  });
  metrics.forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.filter===selected)));
  document.getElementById('visible-count').textContent=`显示 ${visible} / ${cards.length} 条 SQL`;
  const empty=document.getElementById('empty');
  empty.hidden=visible!==0;
  empty.textContent=selected==='attention'&&!query?'没有需关注的 SQL。点击“一致”或“全部样例”查看其他结果。':'没有符合当前筛选条件的 SQL。';
}
metrics.forEach(button=>button.addEventListener('click',()=>{status.value=button.dataset.filter;filter();}));
search.addEventListener('input',filter);status.addEventListener('change',filter);
document.querySelectorAll('.expand-plan').forEach(toggle=>toggle.addEventListener('change',()=>{
  toggle.closest('article').querySelectorAll('.node-details').forEach(detail=>{
    detail.open=toggle.checked||detail.closest('.plan-cell').classList.contains('changed-cell');
  });
}));
filter();
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
    attention = sum(item["compare_status"] != "same" or item.get("requires_review", False) for item in result.items)
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
            diff_parts.append(f'<details class="diff-event" id="{anchor}-diff-{n}" open>'
                              f'<summary>差异 {n} · {escaped(EVENT_LABELS.get(event.type, event.type))} · {escaped(event.field)}</summary>'
                              f'<div class="diff-values"><div class="diff-value old"><small>旧值 · old</small>{old}</div>'
                              f'<div class="diff-value new"><small>新值 · new</small>{new}</div></div>'
                              f'<details><summary>差异证据</summary>{links}<pre>{escaped(primitive(event))}</pre></details></details>')
        diff_html = f'<div class="diff-list"><h3>发现 {len(events)} 处差异</h3>{"".join(diff_parts)}</div>' if events else ''
        status = item["compare_status"]
        needs_attention = status != "same" or item.get("requires_review", False)
        hidden = '' if needs_attention else ' hidden'
        note = ''
        if item.get("needs_sample"):
            note = '<p class="issue-note">缺少可执行样例：需要补充参数或会话变量值，本条 SQL 尚未完成计划比较。</p>'
        elif status in {"not_run", "incomparable"}:
            note = '<p class="issue-note">本条 SQL 未完成完整比较，请展开“排名、来源、覆盖与问题”查看原因。</p>'
        cards.append(f'<article id="{anchor}" data-status="{escaped(status)}" data-attention="{str(needs_attention).lower()}" '
                     f'data-needs-sample="{str(bool(item.get("needs_sample"))).lower()}"{hidden}>'
                     f'<div class="card-heading"><h2><span class="query-number">#{index:02d}</span>{escaped(q.schema or "全限定 SQL")}</h2>'
                     f'<span class="badge {escaped(status)}">{escaped(STATUS_LABELS[status])}</span></div><p class="id">{escaped(q.query_id)}</p>'
                     f'<pre class="sql">{escaped(q.sql_text or "缺少可执行样例")}</pre>'
                     + (f'<details><summary>Prepared 参数样例（重新优化，不复现计划缓存）</summary><pre>{escaped(q.parameters)}</pre></details>' if q.parameters is not None else '')
                     +
                     f'{note}{diff_html}<div class="plan-toolbar"><h3>执行计划</h3>'
                     '<div class="legend"><span><mark class="removed">旧值 / 移除</mark></span><span><mark class="added">新值 / 新增</mark></span>颜色仅表示差异</div>'
                     '<label><input class="expand-plan" type="checkbox"> 展开全部算子详情</label></div>'
                     f'{plan_table(q.query_id, irs, snapshots, events, anchor)}<div class="raw-plans">{"".join(sides)}</div>'
                     f'<details><summary>排名、来源、覆盖与问题</summary><pre>{escaped({"metrics": q.metrics, "origins": q.sample_origins, "item": item})}</pre></details></article>')
    report = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>PCC 执行计划比较报告</title><style>''' + STYLES + '</style></head><body><main><header><div class="heading"><div><p class="eyebrow">PCC · PLAN COMPARISON</p><h1>执行计划比较</h1></div>'
    status_label = {"completed": "完成", "partial": "部分完成", "blocked": "前置条件不足", "interrupted": "已中断"}[result.run_status]
    report += f'<span class="run-status">运行状态：{status_label}</span></div>'
    report += '<p class="subtitle">默认展示需关注的 SQL。点击下方数字卡片，可单独查看一致、变化或未完成的结果。</p>'
    metrics = [("attention", "需关注 · 默认", attention), ("", "全部样例", result.summary.get("selected_samples", "0")),
               *[(key, label, result.summary.get(key, "0")) for key, label in
                 (("same", "一致"), ("changed", "变化"), ("incomparable", "不可完整比较"), ("not_run", "未运行"), ("needs_sample", "缺少样例"))]]
    report += '<div class="metrics" role="group" aria-label="按比较结果筛选">' + "".join(
        f'<button type="button" class="metric" data-filter="{key}" aria-pressed="{str(key == "attention").lower()}">'
        f'<b>{escaped(str(count))}</b><small>{label}</small></button>' for key, label, count in metrics) + '</div>'
    rate = result.summary.get("complete_comparison_rate")
    rate_text = f"{Decimal(rate) * 100:.2f}%" if rate is not None else "N/A"
    report += f'<p class="coverage">已完整比较 {escaped(result.summary.get("full_comparisons", "0"))} 条 · 完整比较率 {rate_text}（包含缺少样例的记录）；业务 SQL 总体覆盖率未知。</p>'
    report += '<p class="coverage">比较声明上下文下的普通 EXPLAIN；计划或估算值变化不代表实际性能变差。生产复现可信度尚未验证。</p>'
    report += f'<div class="report-details"><details><summary>输入范围、筛选口径与环境</summary><pre>{escaped({"selection": selection, "context": context})}</pre></details>'
    report += f'<details><summary>机器摘要与业务指纹</summary><pre>{escaped({"summary": result.summary, "fingerprint": result.fingerprint})}</pre></details>'
    report += f'<details><summary>全部问题与覆盖限制</summary><pre>{escaped(result.issues)}</pre></details></div></header>'
    report += '<div class="filters"><input id="search" type="search" aria-label="搜索 SQL" placeholder="搜索 SQL / 对象 / query_id"><select id="status" aria-label="比较状态"><option value="attention" selected>需关注（默认）</option><option value="">全部状态</option><option value="changed">变化</option><option value="same">一致</option><option value="incomparable">不可完整比较</option><option value="not_run">未运行</option><option value="needs_sample">缺少样例</option></select>'
    report += f'<span id="visible-count" aria-live="polite">显示 {attention} / {len(cards)} 条 SQL</span></div>'
    report += "".join(cards)
    report += f'<div id="empty" class="empty" role="status"{" hidden" if attention else ""}>没有需关注的 SQL。点击“一致”或“全部样例”查看其他结果。</div>'
    report += '</main><script>' + SCRIPT + '</script></body></html>'
    write_bytes(root / "report.html", report.encode())
    write_bytes(root / "differences.csv", buffer.getvalue().encode("utf-8-sig"))
