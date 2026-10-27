from html.parser import HTMLParser

from pcc_tool import api
from pcc_tool.models import PlanIR, PlanNode, QueryCase
from pcc_tool.plan.diff import compare_ir
from pcc_tool.render.report import aligned_rows, highlighted_pair, plan_rows, plan_table, render_reports


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


def test_report_defaults_to_attention_and_exposes_exact_status_filters(tmp_path, snapshot):
    text, result = make_report(tmp_path, snapshot, ['same', 'changed', 'incomparable', 'not_run'])
    tags = Tags(text)
    articles = tags.attrs('article')
    assert [a['data-status'] for a in articles if 'hidden' not in a] == ['changed', 'incomparable', 'not_run']
    assert next(a for a in tags.attrs('option') if 'selected' in a)['value'] == 'attention'
    buttons = tags.attrs('button')
    assert [b['data-filter'] for b in buttons if b['aria-pressed'] == 'true'] == ['attention']
    assert '<b>3</b><small>需关注 · 默认</small>' in text
    assert {b['data-filter'] for b in buttons} >= {'same', 'changed', 'incomparable', 'not_run', 'needs_sample', ''}
    assert [a['data-needs-sample'] for a in articles] == ['false', 'false', 'false', 'true']
    assert result.summary['full_comparisons'] == '2'


def test_all_same_and_empty_reports_show_empty_state(tmp_path, snapshot):
    for states in (['same'], []):
        text, _ = make_report(tmp_path, snapshot, states)
        tags = Tags(text)
        assert all('hidden' in attrs for attrs in tags.attrs('article'))
        empty = next(attrs for attrs in tags.attrs('div') if attrs.get('id') == 'empty')
        assert 'hidden' not in empty
        assert '没有需关注的 SQL' in text


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
