"""Human-readable rule facts and navigation, with no review/approval actions."""
import html

from pcc_tool.plan.findings import RULES, similar_groups


def esc(value):
    return html.escape(str(value if value is not None else "未记录"), quote=True).encode("utf-8", "backslashreplace").decode("utf-8")


MATCH_LABELS = {"equal_subtree": "完整子树相同", "object_operator_attributes": "对象、算子和属性唯一对应",
                "unique_descendants": "完整后代子树唯一对应（不以估算行数匹配）",
                "accessed_objects": "局部访问对象唯一对应", "operator_role": "算子及角色唯一对应",
                "operator": "局部算子唯一对应", "sole_remaining_pair": "局部仅剩一对节点"}


def comparison_method():
    rules = "".join(f'<tr><td><code>{key}</code></td><th>{esc(name)}</th><td>{esc(meaning)}</td></tr>'
                    for key, (name, meaning) in RULES.items())
    return ('<details class="report-panel method-panel"><summary><strong>核心比对逻辑与规则口径</strong></summary>'
            '<div class="report-panel-body"><ol class="method-steps">'
            '<li><b>先校验是否能完整比较。</b>材料、参数、计划解析或必要上下文不足时归入“无法比对”，不会计入差异 SQL 数。</li>'
            '<li><b>按计划树局部匹配节点。</b>在对应父节点的子节点内，依次尝试完整子树、对象＋算子＋属性、完整后代子树（保留谓词、忽略估算行数）、访问对象、算子＋角色、算子；每步只采用双方唯一的对应。最后只剩一对时局部配对。单子节点包装层增删单独识别。仍有歧义时保留观察并要求复核。</li>'
            '<li><b>保留算子语义。</b>统一 row / JSON 列名、数值格式和已知算子临时编号及引用；不把 FullScan 与 RangeScan、HashJoin 与 MergeJoin 合成同一种算子。当前没有未经验证的跨版本算子等价映射，未知算子或字段按无法完整比较处理。</li>'
            '<li><b>比较属性并归并事实。</b>比较具体算子、表/索引/分区、任务与引擎、Join 角色、子节点顺序、谓词属性和 estRows。同一 SQL 同一访问位置的扫描、父级链路及对象变化归并，保留规则及全部字段证据；其他位置分别计数。估算行数单列为辅助指标。</li>'
            '<li><b>相似分组只辅助浏览。</b>按差异类别、规则及旧新算子结构分组，不以表名、谓词或估算数值决定相似；每条 SQL 的数据规模、分布及业务场景仍需 DBA 单独复核。没有批量通过或结论继承。</li>'
            '</ol><p class="overview-note">规则版本 1。规则识别事实，不给出性能优劣或自动批准结论。'
            '仅 estRows 变化也属于“存在差异”，但只计入辅助指标，不计入访问路径等计划事实项。</p>'
            '<details><summary>查看固定规则及含义</summary><div class="overview-table-wrap"><table class="overview-table">'
            '<thead><tr><th>规则</th><th>名称</th><th>判定含义</th></tr></thead><tbody>' + rules + '</tbody></table></div></details></div></details>')


def unavailable_reason(item, labels):
    if item["compare_status"] in {"same", "changed"}:
        return ""
    reasons = []
    for issue in item.get("issues", []):
        if issue.get("severity") == "warning":
            continue
        code = issue.get("code", "UNKNOWN")
        label = labels.get(code, code)
        side = issue.get("side")
        text = (f'{side}：' if side else '') + label
        if issue.get("message"):
            text += ' · ' + str(issue["message"])
        if issue.get("cause"):
            text += ' · ' + str(issue["cause"])
        reasons.append((code, text))
    if not reasons and item.get("needs_sample"):
        reasons.append(("NEEDS_EXECUTABLE_SAMPLE", "缺少可执行 SQL 样例或完整参数，请补充样例。"))
    if not reasons:
        for side in ("old", "new"):
            state = item.get(side, {})
            reasons.append((side, f'{side}：采集状态 {state.get("capture_status", "未记录")}；解析状态 {state.get("parse_status", "未记录")}'))
    rows = "".join(f'<li>{esc(text)} <code>{esc(code)}</code></li>' for code, text in dict.fromkeys(reasons))
    short = '、'.join(dict.fromkeys(labels.get(code, code) for code, _ in reasons))
    return ('<section class="unavailable-reason"><div><strong>无法比对的原因</strong><span>' + esc(short) + '</span></div>'
            '<details><summary>查看详情</summary><ul>' + rows + '</ul>'
            '<p>不计入差异或完整比较；保留已取得的计划供排查。</p></details></section>')


def _nodes(nodes, anchor, side, auxiliary=False, show_attributes=False):
    if not nodes:
        return '<span class="muted">此侧无对应节点</span>'
    parts = []
    for node in nodes:
        link = f'#{anchor}-{side}-{node["node_id"]}'
        indent = '　' * int(node.get("depth", 0))
        value = (f'<span class="estimate-value">estRows：{esc(node["estimate"])}</span>' if auxiliary else
                 f'<span>{esc(node["access"] or "未列访问对象")} · {esc(node["task"])}</span>')
        attributes = (f'<div class="node-info">{esc(node["attributes"])}</div>' if show_attributes else '')
        name = indent + node["operator"] + (f' ({node["role"]})' if node["role"] else '')
        parts.append(f'<div class="fact-node"><a href="{esc(link)}"><code>{esc(name)}</code></a>'
                     f'{value}{attributes}</div>')
    return "".join(parts)


def findings_panel(item, events, anchor):
    facts = item.get("findings", [])
    if not facts:
        return ""
    complete = item["compare_status"] == "changed"
    sections = []
    for kind, title in (("plan", "差异类型及事实"), ("auxiliary", "辅助指标 · 估算行数")):
        selected = [f for f in facts if f["kind"] == kind]
        if not selected:
            continue
        cards = []
        for fact in selected:
            rules = []
            for rule in fact["rules"]:
                links = "、".join(f'<a href="#{anchor}-diff-{n}">字段 {n}</a>' for n in rule["event_refs"])
                rules.append(f'<li><code>{esc(rule["id"])}</code> <b>{esc(rule["name"])}</b>：{esc(rule["meaning"])} '
                             f'<span class="rule-evidence">{links}</span></li>')
            matches = []
            for number in fact["event_refs"]:
                event = events[int(number) - 1]
                match = event.evidence.get("correspondence")
                if match:
                    matches.append(f'{event.old_node} ↔ {event.new_node}：{MATCH_LABELS.get(match["method"], match["method"])}')
            matching = '<p class="overview-note">节点对应依据：' + esc('；'.join(dict.fromkeys(matches))) + '</p>' if matches else ''
            rule_count, event_count = len(fact["rules"]), len(fact["event_refs"])
            count_label = f'归并为 1 项 · {rule_count} 条规则 · {event_count} 条字段记录' if complete else f'未确认观察 · {event_count} 条字段记录'
            cards.append(f'<section class="finding" id="{anchor}-{esc(fact["finding_id"])}" data-finding-kind="{kind}">'
                         f'<div class="issue-heading"><strong>{esc(fact["title"])}</strong><span>{count_label}</span></div>'
                         '<div class="fact-pair"><div><small>old · 升级前</small>' + _nodes(fact["old"], anchor, "old", kind == "auxiliary", fact["category"] in {"attribute", "join"})
                         + '</div><div><small>new · 升级后</small>' + _nodes(fact["new"], anchor, "new", kind == "auxiliary", fact["category"] in {"attribute", "join"}) + '</div></div>'
                         + '<details class="rule-hits"><summary>规则与依据</summary><ul>' + ''.join(rules) + '</ul></details>'
                         + matching + '</section>')
        title = title if complete else '未确认观察 · ' + title
        note = ('估算值不代表实际扫描行数或性能；这些记录不并入访问路径变化。' if kind == 'auxiliary' else
                '同一访问位置的相关规则归并计数，保留全部证据；结论待 DBA 逐条评审。')
        sections.append(f'<section class="finding-section {kind}"><h3>{title} · {len(selected)} 项</h3>'
                        f'<p class="overview-note">{note}</p>{"".join(cards)}</section>')
    counts = {}
    short_titles = {'access_path': '访问路径', 'attribute': '属性', 'join': '连接方式',
                    'engine': '任务 / 引擎', 'order': '子节点顺序', 'operator': '算子', 'structure': '计划结构'}
    for fact in facts:
        if fact['kind'] == 'plan':
            name = short_titles.get(fact['category'], fact['title'])
            counts[name] = counts.get(name, 0) + 1
    glance = ' · '.join(f'{name} {count}' for name, count in list(counts.items())[:3])
    if len(counts) > 3:
        glance += f' · 另 {len(counts)-3} 类'
    auxiliary = sum(f['kind'] == 'auxiliary' for f in facts)
    auxiliary_html = f'<span class="aux-count">估算变化 {auxiliary}</span>' if auxiliary else ''
    return (f'<details class="finding-review"><summary><strong>{"差异摘要" if complete else "未确认观察"}</strong>'
            f'<span class="finding-glance">{esc(glance)}</span>{auxiliary_html}<span class="disclosure-action">展开</span></summary>'
            + ''.join(sections) + '</details>')


def grouping_panel(items, queries):
    groups = similar_groups(items)
    if not groups:
        return ""
    positions = {item["query_id"]: i for i, item in enumerate(items, 1)}
    by_query = {q.query_id: q for q in queries}
    facts = {f["finding_id"]: f for item in items for f in item.get("findings", [])}
    output = []
    for index, group in enumerate(groups, 1):
        rows = []
        for member in group["members"]:
            qid = member["query_id"]
            fact = facts[member["finding_id"]]
            objects = list(dict.fromkeys(n["access"] for side in ("old", "new") for n in fact[side] if n["access"]))
            rows.append(f'<tr><td><a data-query-link href="#query-{positions[qid]}">SQL #{positions[qid]:02d}</a></td>'
                        f'<td>{esc(by_query[qid].schema)}</td><td>{esc("；".join(objects) or "未列访问对象")}</td>'
                        '<td>待逐条评审</td></tr>')
        members = ','.join(f'query-{positions[qid]}' for qid in group["query_ids"])
        output.append(f'<details class="similar-group"><summary>组 {index} · {esc(group["title"])} · '
                      f'{len(group["query_ids"])} 条 SQL / {len(group["members"])} 项事实</summary>'
                      f'<button type="button" data-group="{members}">集中查看本组 SQL</button>'
                      '<table class="overview-table"><thead><tr><th>SQL</th><th>数据库</th><th>涉及对象</th><th>评审</th></tr></thead>'
                      '<tbody>' + ''.join(rows) + '</tbody></table></details>')
    return (f'<details class="grouping"><summary>相似差异分组 · {len(groups)} 组</summary><p>仅按规则和算子结构归类，便于集中查看。'
            '<b>不代表自动批量通过，也不继承其他 SQL 的评审结论。</b>请分别核对表的数据规模、分布、参数和业务场景。</p>'
            + ''.join(output) + '</details>')
