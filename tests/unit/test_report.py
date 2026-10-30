from html.parser import HTMLParser
import json
import re

from pcc_tool import api
from pcc_tool.models import ComparisonResult, PlanIR, PlanNode, PlanSnapshot, QueryCase
from pcc_tool.plan.diff import compare_ir
from pcc_tool.render.report import aligned_rows, highlighted_pair, plan_rows, plan_table, render_reports, report_overview


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def attrs(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


def make_report(tmp_path, snapshot, states):
    queries, snapshots = [], {}
    for i, state in enumerate(states):
        q = QueryCase.create(None if state == 'not_run' else f'SELECT {i}', 'demo',
                             origin={'index': str(i)}, executable_status='needs_sample' if state == 'not_run' else 'ok')
        queries.append(q)
        if state == 'not_run':
            continue
        for side in ('old', 'new'):
            info = 'rows:2' if side == 'new' and state == 'changed' else 'rows:1'
            snap = snapshot([['TableDual_1', '1', 'root', '', info]], side=side, query_id=q.query_id)
            if state == 'incomparable':
                snap.capture_status = 'failed'
            snapshots[q.query_id, side] = snap
    result, irs = api._summarize(queries, snapshots, 'test', [])
    render_reports(tmp_path, result, queries, snapshots, irs, {}, {})
    return (tmp_path / 'report.html').read_text(), result


def test_report_has_three_exclusive_outcomes_and_defaults_to_all(tmp_path, snapshot):
    text, result = make_report(tmp_path, snapshot, ['same', 'changed', 'incomparable', 'not_run'])
    tags = Tags(text)
    assert [a['data-status'] for a in tags.attrs('article')] == ['same']
    records = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', text, re.S)[1])
    articles = [Tags(record['html']).attrs('article')[0] for record in records]
    assert [a['data-status'] for a in articles] == ['same', 'changed', 'incomparable', 'not_run']
    assert [a['data-category'] for a in articles] == ['same', 'changed', 'unavailable', 'unavailable']
    assert next(a for a in tags.attrs('option') if 'selected' in a)['value'] == ''
    buttons = [b for b in tags.attrs('button') if 'data-filter' in b]
    assert [b['data-filter'] for b in buttons if b['aria-pressed'] == 'true'] == ['']
    assert '<b>1</b><small>存在差异</small>' in text
    assert '<b>2</b><small>无法比对</small>' in text
    assert {b['data-filter'] for b in buttons} == {'same', 'changed', 'unavailable', ''}
    assert [a['data-needs-sample'] for a in articles] == ['false', 'false', 'false', 'true']
    assert result.summary['full_comparisons'] == '2'
    assert result.summary['unavailable'] == '2'
    assert '无法比对的原因' in text and '核心比对逻辑与规则口径' in text


def test_report_focuses_plan_and_keeps_rules_and_evidence_available_on_demand(tmp_path, snapshot):
    text, _ = make_report(tmp_path, snapshot, ['changed'])
    tags = Tags(text)
    for name in ('report-context', 'finding-review', 'rule-hits', 'technical-details'):
        details = [attrs for attrs in tags.attrs('details') if attrs.get('class') == name]
        assert details and all('open' not in attrs for attrs in details)
    assert '核心比对逻辑与规则口径' in text and '差异证据' in text
    assert {b['data-copy-plan'] for b in tags.attrs('button') if 'data-copy-plan' in b} == {'old', 'new', 'both'}
    assert {b['id'] for b in tags.attrs('button') if b.get('id') in {'previous-query','next-query'}} == {'previous-query','next-query'}


def test_all_same_remain_visible_and_only_empty_report_has_empty_state(tmp_path, snapshot):
    for states in (['same'], []):
        text, _ = make_report(tmp_path, snapshot, states)
        tags = Tags(text)
        assert all('hidden' not in attrs for attrs in tags.attrs('article'))
        empty = next(attrs for attrs in tags.attrs('div') if attrs.get('id') == 'empty')
        assert ('hidden' in empty) == bool(states)
        assert '没有符合当前筛选条件的 SQL' in text


def test_changed_fields_are_marked_without_losing_text_or_escaping():
    before = 'keep order:false'
    after = 'keep order:false, stats:partial[id:unInitialized] <script>alert(1)</script>'
    old, new = highlighted_pair(before, after)
    assert old == before
    assert '<mark class="added">' in new
    assert 'stats:partial[id:unInitialized]' in new
    assert '<script>' not in new and '&lt;script&gt;' in new
    # Removing the presentation tags restores both original values exactly.
    from html import unescape
    import re
    assert unescape(re.sub(r'</?mark[^>]*>', '', new)) == after
    assert highlighted_pair('0', '0') == ('0', '0')


def test_plan_alignment_keeps_inserted_wrapper_separate():
    old_leaf = PlanNode('n0', 'TableFullScan', task='cop[tikv]', access='table:t', estimate='0')
    new_leaf = PlanNode('n1', 'TableFullScan', task='cop[tikv]', access='table:t', estimate='0')
    old = PlanIR('q', [old_leaf])
    new = PlanIR('q', [PlanNode('n0', 'Selection', children=[new_leaf])])
    events, _ = compare_ir(old, new)
    pairs = aligned_rows(plan_rows(old), plan_rows(new), events)
    assert [(a[0].operator if a else None, b[0].operator if b else None) for a, b in pairs] == [
        (None, 'Selection'), ('TableFullScan', 'TableFullScan')]
    table = plan_table('q', {('q', 'old'): old, ('q', 'new'): new}, {}, events, 'query-1')
    assert '新增算子' in table and '└─' in table
    assert 'estRows</small> 0' in table


def test_only_recorded_changed_field_is_highlighted_and_expanded():
    old = PlanIR('q', [PlanNode('n0', 'TableFullScan', attributes='keep order:false')])
    new = PlanIR('q', [PlanNode('n7', 'TableFullScan', attributes='keep order:false, stats:partial[id:unInitialized]')])
    events, _ = compare_ir(old, new)
    table = plan_table('q', {('q', 'old'): old, ('q', 'new'): new}, {}, events, 'query-1')
    assert table.count('changed-cell') == 2
    assert table.count('class="node-details" open') == 2
    assert '<mark class="added">, stats:partial[id:unInitialized]</mark>' in table
    assert '<mark class="added">TableFullScan</mark>' not in table
    assert 'query-1-old-n0' in table and 'query-1-new-n7' in table


def test_estimate_only_change_does_not_expand_unchanged_attributes():
    old = PlanIR('q', [PlanNode('n0', 'TableFullScan', estimate='10', attributes='keep order:false')])
    new = PlanIR('q', [PlanNode('n1', 'TableFullScan', estimate='20', attributes='keep order:false')])
    events, _ = compare_ir(old, new)
    table = plan_table('q', {('q','old'):old, ('q','new'):new}, {}, events, 'query-1')
    details = [d for d in Tags(table).attrs('details') if d.get('class') == 'node-details']
    assert len(details) == 2 and all('open' not in d and d['data-default-open']=='false' for d in details)
    assert 'class="added"' in table and 'class="removed"' in table


def test_reordered_nodes_preserve_each_sides_order_and_do_not_invent_additions():
    a = PlanNode('a', 'TableFullScan', access='table:a')
    b = PlanNode('b', 'TableFullScan', access='table:b')
    old, new = PlanIR('q', [a, b]), PlanIR('q', [b, a])
    events, _ = compare_ir(old, new)
    pairs = aligned_rows(plan_rows(old), plan_rows(new), events)
    assert [row[0].access for row, _ in pairs if row] == ['table:a', 'table:b']
    assert [row[0].access for _, row in pairs if row] == ['table:b', 'table:a']
    assert not any(e.type in {'node_added', 'node_removed'} for e in events)


def test_unknown_fields_and_one_sided_plans_remain_visible():
    node = PlanNode('n0', 'FutureOperator', unknown={'future': '<img src=x onerror=alert(1)>'})
    table = plan_table('q', {('q', 'new'): PlanIR('q', [node])}, {}, [], 'query-9')
    assert 'FutureOperator' in table and '其他字段' in table
    assert '<img' not in table and '&lt;img' in table
    assert '此侧无对应行' in table


def test_large_changed_text_is_preserved_without_character_matching():
    old, new = highlighted_pair('x' * 12000, 'y' * 12000 + '<svg>')
    assert 'x' * 12000 in old and 'y' * 12000 in new
    assert '<svg>' not in new and '&lt;svg&gt;' in new


def test_overview_explains_scope_and_groups_related_issues():
    query = QueryCase.create('SELECT 1', 'demo', origin={'source_cluster': 'source-a',
                             'summary_begin_time': '2026-10-28T17:30:00+08:00',
                             'summary_end_time': '2026-10-28T18:00:00+08:00'})
    probe = QueryCase.create('select @@version_comment limit 1', 'demo', executable_status='needs_sample')
    summary = {'digest_count': '2', 'selected_samples': '2', 'executable_samples': '1', 'both_captured': '1',
               'full_comparisons': '1', 'needs_sample': '1', 'events': '0', 'complete_comparison_rate': '0.5',
               'coverage_denominator': 'selected_samples_including_unresolved', 'business_coverage': 'unknown'}
    missing = {'code': 'NEEDS_EXECUTABLE_SAMPLE', 'message': '未绑定参数或会话变量需要补充样例', 'query_id': probe.query_id}
    context_difference = {'code': 'CONTEXT_DIFFERENCE', 'message': '两端上下文不同', 'query_id': query.query_id,
                          'old': {'tidb_enable_plan_cache_for_param_limit': None},
                          'new': {'tidb_enable_plan_cache_for_param_limit': 'ON'}, 'severity': 'warning'}
    result = ComparisonResult('abc123', summary, [{'query_id': query.query_id}, {'query_id': probe.query_id}], [],
                              [missing, {**missing, 'side': 'old'}, {**missing, 'side': 'new'}, context_difference], 'partial')
    selection = {'begin': '2026-10-28T17:50:49+08:00', 'end': '2026-10-28T17:50:50+08:00',
                 'schemas': ['demo'], 'statement_types': ['select'], 'ranking_scope': 'global', 'top_n': '10',
                 'order_by': ['execution_count', 'average_keys'], 'window_semantics': 'overlapping_cumulative_windows',
                 'coverage': 'visible_summary_only', 'custom_selection_field': 'preserved'}
    context = {'version': 'v6.5.12', 'stats_time_semantics': 'export_time_not_business_window',
               'collection': {'issues': [{'code': 'INSTANCE_NO_VISIBLE_ROWS', 'message': '无法区分无流量与缺采',
                                           'instance': '127.0.0.1:4000'}]}}
    snapshots = {(query.query_id, 'old'): PlanSnapshot(query.query_id, 'old', version='v6.5.12'),
                 (query.query_id, 'new'): PlanSnapshot(query.query_id, 'new', version='v8.5.5')}
    text = report_overview(selection, context, result, [query, probe], snapshots)
    assert '<pre' not in text
    assert all(label in text for label in ('采集范围与环境', '比较摘要与追溯标识', '问题与覆盖限制'))
    assert '2026-10-28 17:50:49+08:00' in text and '全局排名，每项取前 10 名' in text
    assert '执行次数、平均扫描 Key 数（估算）' in text
    assert '实际摘要窗口' in text and '2026-10-28 17:30:00+08:00' in text and 'source-a' in text
    assert '完整比较率' in text and '50.00%' in text
    assert '不是区间内精确流量' in text and '导出时的状态' in text
    assert 'v6.5.12' in text and 'v8.5.5' in text and 'abc123' in text
    assert '涉及 1 条 SQL · 3 条记录' in text and 'select @@version_comment limit 1' in text
    assert '已记录 SQL 文本，仍需核对语句完整性、参数或会话上下文。' in text
    assert 'data-query-link href="#query-2"' in text
    assert '参数化 LIMIT 的计划缓存' in text and 'tidb_enable_plan_cache_for_param_limit' in text
    assert '未记录</td><td>开启（ON）' in text
    assert '源端采集提醒 · 1 条记录' in text and '127.0.0.1:4000' in text
    assert 'custom_selection_field' in text and 'preserved' in text


def test_overview_preserves_unknown_details_and_escapes_user_content():
    query = QueryCase.create('SELECT \'<script>alert(1)</script>\'', '<schema>')
    issue = {'code': 'FUTURE_ISSUE', 'message': '<img src=x onerror=alert(1)>', 'query_id': query.query_id,
             'custom_field': {'future': '<unsafe>'}}
    missing = {'code': 'NEEDS_EXECUTABLE_SAMPLE', 'message': '需要补充样例', 'query_id': query.query_id}
    schema_issue = {'code': 'SCHEMA_MISMATCH', 'object': ['demo', 'orders'], 'side': 'new'}
    result = ComparisonResult('<fingerprint>', {}, [{'query_id': query.query_id}], [], [issue, missing, schema_issue], 'partial')
    text = report_overview({'schemas': ['<schema>']}, {}, result, [query], {})
    assert '<img' not in text and '&lt;img' in text
    assert '<script>' not in text and '&lt;script&gt;' in text
    assert '<schema>' not in text and '&lt;schema&gt;' in text
    assert '<fingerprint>' not in text and '&lt;fingerprint&gt;' in text
    assert 'FUTURE_ISSUE' in text and 'custom_field' in text and '&lt;unsafe&gt;' in text
    assert '目标表结构与来源不一致' in text and '两端表结构不一致' not in text
    assert '未指定或未记录' in text and '未获取' in text


def test_context_differences_keep_values_associated_with_the_right_queries():
    first, second = QueryCase.create('SELECT 1', 'demo'), QueryCase.create('SELECT 2', 'demo')
    issues = [{'code': 'CONTEXT_DIFFERENCE', 'message': '两端上下文不同', 'query_id': first.query_id,
               'old': {'sql_mode': 'A'}, 'new': {'sql_mode': 'B'}},
              {'code': 'CONTEXT_DIFFERENCE', 'message': '两端上下文不同', 'query_id': second.query_id,
               'old': {'sql_mode': 'C'}, 'new': {'sql_mode': 'D'}}]
    result = ComparisonResult('fp', {}, [{'query_id': first.query_id}, {'query_id': second.query_id}], [], issues, 'partial')
    text = report_overview({}, {}, result, [first, second], {})
    assert '<th scope="row">SQL 模式<code>sql_mode</code></th><td>A</td><td>B</td><td>#01</td>' in text
    assert '<th scope="row">SQL 模式<code>sql_mode</code></th><td>C</td><td>D</td><td>#02</td>' in text
