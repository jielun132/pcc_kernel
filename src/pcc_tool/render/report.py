import csv
import html
import io
from decimal import Decimal

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


def render_reports(root, result, queries, snapshots, irs, selection, context):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["query_id", "schema", "compare_status", "sql_text", "event", "field", "old", "new", "requires_review", "parameters"])
    diffs_by_query = {}
    for event in result.diffs:
        diffs_by_query.setdefault(event.query_id, []).append(event)
    query_by_id = {q.query_id: q for q in queries}
    cards = []
    for item in result.items:
        q = query_by_id[item["query_id"]]
        events = diffs_by_query.get(q.query_id, [])
        for event in events or [None]:
            writer.writerow([csv_safe(v) for v in (q.query_id, q.schema, item["compare_status"], q.sql_text,
                event.type if event else "", event.field if event else "", canonical(event.old) if event else "",
                canonical(event.new) if event else "", event.requires_review if event else item.get("requires_review", False),
                canonical(q.parameters) if q.parameters is not None else "")])
        sides = []
        for side in ("old", "new"):
            snapshot = snapshots.get((q.query_id, side))
            ir = irs.get((q.query_id, side))
            sides.append(f'<section><h3>{side} · {escaped(snapshot.version if snapshot else "未采集")}</h3>'
                         f'<pre>{escaped(tree_text(ir))}</pre><details><summary>上下文、原始计划与警告</summary>'
                         f'<pre>{escaped(primitive(snapshot) if snapshot else {})}</pre></details></section>')
        diff_html = "".join(f'<details><summary>{escaped(EVENT_LABELS[e.type])} · {escaped(e.field)}</summary>'
                            f'<p>旧：<code>{escaped(e.old)}</code></p><p>新：<code>{escaped(e.new)}</code></p>'
                            f'<pre>{escaped(primitive(e))}</pre></details>' for e in events)
        cards.append(f'<article data-status="{escaped(item["compare_status"])}"><h2>{escaped(q.schema or "全限定 SQL")} '
                     f'<span>{escaped(STATUS_LABELS[item["compare_status"]])}</span></h2><p class="id">{escaped(q.query_id)}</p>'
                     f'<pre class="sql">{escaped(q.sql_text or "缺少可执行样例")}</pre>'
                     + (f'<details><summary>Prepared 参数样例（重新优化，不复现计划缓存）</summary><pre>{escaped(q.parameters)}</pre></details>' if q.parameters is not None else '')
                     +
                     f'<details><summary>排名、来源、覆盖与问题</summary><pre>{escaped({"metrics": q.metrics, "origins": q.sample_origins, "item": item})}</pre></details>'
                     f'<div class="plans">{"".join(sides)}</div>{diff_html or "<p>没有观察到差异事件；是否可完整比较以状态为准。</p>"}</article>')
    report = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>PCC 执行计划比较报告</title><style>
body{font:15px/1.6 system-ui,sans-serif;margin:0;background:#f1f5f9;color:#172438}main{max-width:1500px;margin:auto;padding:32px}
h1{font-size:30px;margin:0}h2{font-size:18px}h3{font-size:15px}header,article{background:white;border:1px solid #d9e1eb;border-radius:10px;padding:24px;margin-bottom:20px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,monospace;background:#f8fafc;padding:14px;border-radius:5px;max-height:650px;overflow:auto}
.plans{display:grid;grid-template-columns:1fr 1fr;gap:16px}.plans section{min-width:0}span{font-size:13px;background:#e1eafa;padding:4px 9px;border-radius:5px}
article[data-status="incomparable"]{border-left:5px solid #c77d12}article[data-status="not_run"]{border-left:5px solid #9e303c}
article[data-status="changed"]{border-left:5px solid #3169ce}.id{font:11px monospace;color:#66758c;overflow-wrap:anywhere}summary{cursor:pointer}
input,select{font:inherit;padding:8px;margin-right:10px;border:1px solid #acbbd1;border-radius:5px}input{min-width:260px}
.metrics{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}.metric{background:#f1f5fa;padding:12px 20px;flex:1;min-width:110px}.metric b{display:block;font-size:25px}.metric small{color:#52647d}
@media(max-width:800px){.plans{grid-template-columns:1fr}main{padding:12px}}</style><main><header><h1>PCC 执行计划比较</h1>'''
    status_label = {"completed": "完成", "partial": "部分完成", "blocked": "前置条件不足", "interrupted": "已中断"}[result.run_status]
    report += f'<p>运行状态：{status_label}</p>'
    report += '<p>本报告比较声明上下文下的普通 EXPLAIN。估算行数或 cost 的变化不代表实际性能变化；生产复现可信度尚未验证。</p>'
    metric_labels = {"selected_samples": "选定样例", "full_comparisons": "完整比较", "same": "一致", "changed": "变化",
                     "incomparable": "不可完整比较", "not_run": "未运行", "needs_sample": "缺少样例"}
    report += '<div class="metrics">' + "".join(f'<div class="metric"><b>{escaped(result.summary.get(k, "0"))}</b><small>{label}</small></div>' for k, label in metric_labels.items()) + '</div>'
    rate = result.summary.get("complete_comparison_rate")
    rate_text = f"{Decimal(rate) * 100:.2f}%" if rate is not None else "N/A"
    report += f'<p>完整比较率 {rate_text}，分母包含缺少可执行样例的记录。业务 SQL 总体覆盖率未知。</p>'
    report += f'<details><summary>输入范围、筛选口径与环境</summary><pre>{escaped({"selection": selection, "context": context})}</pre></details>'
    report += f'<details><summary>机器摘要与业务指纹</summary><pre>{escaped({"summary": result.summary, "fingerprint": result.fingerprint})}</pre></details>'
    report += f'<details><summary>全部问题与覆盖限制</summary><pre>{escaped(result.issues)}</pre></details></header>'
    report += '<p><input id="search" aria-label="搜索 SQL" placeholder="搜索 SQL / 对象 / query_id"><select id="status" aria-label="比较状态"><option value="">全部状态</option><option value="changed">变化</option><option value="same">一致</option><option value="incomparable">不可完整比较</option><option value="not_run">未运行</option></select></p>'
    report += "".join(cards)
    report += '''</main><script>const s=document.getElementById('search'),t=document.getElementById('status');
function filter(){const q=s.value.toLowerCase();document.querySelectorAll('article').forEach(a=>a.hidden=!!((t.value&&a.dataset.status!==t.value)||!a.textContent.toLowerCase().includes(q)))}s.addEventListener('input',filter);t.addEventListener('change',filter);</script></html>'''
    write_bytes(root / "report.html", report.encode())
    write_bytes(root / "differences.csv", buffer.getvalue().encode("utf-8-sig"))
