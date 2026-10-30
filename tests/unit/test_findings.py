from pcc_tool import api
from pcc_tool.collector.summary import make_query
from pcc_tool.models import QueryCase
from pcc_tool.plan.findings import similar_groups
from pcc_tool.render.report import render_reports


def access_rows(table, new=False, estimates=('10', '10')):
    reader, scan = ('TableReader', 'TableRangeScan') if new else ('IndexReader', 'IndexRangeScan')
    obj = f'table:{table}' + ('' if new else ', index:idx_id(id)')
    return [[reader + '_1', estimates[0], 'root', '', f'{"data" if new else "index"}:{scan}_2'],
            ['└─' + scan + '_2', estimates[1], 'cop[tikv]', obj, 'range:[1,3], keep order:false']]


def summarize(snapshot, cases):
    queries, snapshots = [], {}
    for i, (before, after, issues) in enumerate(cases):
        q = QueryCase.create(f'SELECT {i}', 'demo')
        queries.append(q)
        for side, rows in (('old', before), ('new', after)):
            if rows is not None:
                s = snapshot(rows, side=side, query_id=q.query_id)
                s.issues = list(issues)
                snapshots[q.query_id, side] = s
    result, irs = api._summarize(queries, snapshots, 'fp', [])
    return result, queries, snapshots, irs


def test_access_chain_rules_merge_and_estimates_remain_separate(snapshot):
    result, _, _, _ = summarize(snapshot, [(access_rows('orders'), access_rows('orders', True, ('80', '100')), [])])
    assert result.summary['changed'] == '1' and result.summary['unavailable'] == '0'
    facts = result.items[0]['findings']
    plans = [f for f in facts if f['kind'] == 'plan']
    aux = [f for f in facts if f['kind'] == 'auxiliary']
    assert len(plans) == 1 and len(aux) == 2
    assert plans[0]['category'] == 'access_path'
    assert {'ACCESS_SCAN', 'ACCESS_READER', 'ACCESS_OBJECT', 'ACCESS_INDEX'} <= {r['id'] for r in plans[0]['rules']}
    assert [n['operator'] for n in plans[0]['old']] == ['IndexReader', 'IndexRangeScan']
    assert [n['operator'] for n in plans[0]['new']] == ['TableReader', 'TableRangeScan']
    assert 'index:idx_id(id)' in plans[0]['old'][1]['access']
    assert all(result.diffs[n - 1].type != 'estimate_changed' for n in plans[0]['event_refs'])
    # Every field event is retained exactly once in fact membership; multiple rule hits do not inflate facts.
    refs = [n for f in facts for n in f['event_refs']]
    assert sorted(refs) == list(range(1, len(result.diffs) + 1))
    assert len(refs) == len(set(refs))
    assert all(e.compare_status == 'changed' for e in result.diffs)
    assert all(e.evidence['correspondence']['method'] == 'accessed_objects' for e in result.diffs)
    assert result.summary['plan_findings'] == '1' and result.summary['auxiliary_findings'] == '2'


def test_reader_removal_does_not_lose_access_chain_classification(snapshot):
    old = access_rows('orders')
    new = [['IndexRangeScan_8', '10', 'root', 'table:orders, index:idx_id(id)', 'range:[1,3], keep order:false']]
    result, _, _, _ = summarize(snapshot, [(old, new, [])])
    facts = [f for f in result.items[0]['findings'] if f['category'] == 'access_path']
    assert len(facts) == 1
    assert 'ACCESS_READER' in {r['id'] for r in facts[0]['rules']}
    assert 'ACCESS_INDEX' not in {r['id'] for r in facts[0]['rules']}
    assert any(f['category'] == 'engine' for f in result.items[0]['findings'])


def test_different_access_occurrences_in_one_sql_are_not_merged(snapshot):
    def joined(new):
        rows = [['HashJoin_0', '10', 'root', '', 'inner join']]
        for i, table in enumerate(('orders', 'customers')):
            branch = access_rows(table, new)
            branch[0][0] = ('├─' if i == 0 else '└─') + branch[0][0].replace('_1', f'_{i * 2 + 1}')
            branch[0][4] = branch[0][4].replace('_2', f'_{i * 2 + 2}')
            branch[1][0] = '  ' + branch[1][0].replace('_2', f'_{i * 2 + 2}')
            rows.extend(branch)
        return rows
    result, _, _, _ = summarize(snapshot, [(joined(False), joined(True), [])])
    facts = [f for f in result.items[0]['findings'] if f['category'] == 'access_path']
    assert len(facts) == 2
    assert similar_groups(result.items) == []  # Repeated occurrences are not multiple SQL statements.


def test_incomparable_observations_never_count_as_confirmed_changes(snapshot, tmp_path):
    result, queries, snapshots, irs = summarize(snapshot, [
        (access_rows('a'), access_rows('a', True), [{'code': 'STATS_NOT_READY', 'message': '统计未就绪'}]),
        (access_rows('b'), access_rows('b', True), []),
        (access_rows('c'), None, []),
    ])
    missing = make_query('SELECT * FROM orders WHERE id = ?', 'demo')
    queries.append(missing)
    result, irs = api._summarize(queries, snapshots, 'fp', [])
    assert result.summary['changed'] == '1' and result.summary['unavailable'] == '3'
    assert result.summary['full_comparisons'] == '1' and result.summary['needs_sample'] == '1'
    assert int(result.summary['confirmed_events']) > 0 and int(result.summary['unconfirmed_events']) > 0
    assert result.summary['plan_findings'] == '1'
    assert all(not f['confirmed'] for f in result.items[0]['findings'])
    assert similar_groups(result.items) == []
    render_reports(tmp_path, result, queries, snapshots, irs, {}, {})
    text = (tmp_path / 'report.html').read_text()
    assert '<b>1</b><small>存在差异</small>' in text and '<b>3</b><small>无法比对</small>' in text
    assert '未确认的字段观察（不计入差异）' in text and '统计未就绪' in text
    assert '缺少可执行 SQL 样例或完整参数' in text


def test_similar_groups_exclude_estimates_and_do_not_transfer_review(snapshot):
    result, _, _, _ = summarize(snapshot, [
        (access_rows('small'), access_rows('small', True, ('12', '15')), []),
        (access_rows('large', estimates=('100000', '100000')), access_rows('large', True, ('900000', '900000')), []),
    ])
    groups = similar_groups(result.items)
    assert len(groups) == 1 and len(groups[0]['query_ids']) == 2 and len(groups[0]['members']) == 2
    assert groups[0]['automatic_approval'] is False and groups[0]['review_status'] == 'pending_review'
    assert all(f['review_status'] == 'pending_review' for item in result.items for f in item['findings'])
    assert len({item['findings'][0]['finding_id'] for item in result.items}) == 2


def test_estimate_only_is_auxiliary_not_access_change(snapshot):
    result, _, _, _ = summarize(snapshot, [(access_rows('a'), access_rows('a', estimates=('20', '20')), [])])
    assert result.summary['changed'] == '1'
    assert result.summary['plan_findings'] == '0' and result.summary['auxiliary_findings'] == '2'
    assert all(f['category'] == 'estimate' for f in result.items[0]['findings'])
    assert similar_groups(result.items) == []


def test_unknown_operator_and_ambiguous_matching_stay_unavailable(snapshot):
    unknown = [['FutureOperator_1', '1', 'root', '', '']]
    unknown_new = [['FutureOperator_2', '2', 'root', '', '']]
    old = [['HashJoin_1', '1', 'root', '', 'inner join'],
           ['├─TableFullScan_2', '10', 'cop[tikv]', 'table:t', ''],
           ['└─TableFullScan_3', '10', 'cop[tikv]', 'table:t', '']]
    new = [list(row) for row in old]
    new[1][1] = '20'
    result, _, _, _ = summarize(snapshot, [(unknown, unknown_new, []), (old, new, [])])
    assert result.summary['unavailable'] == '2' and result.summary['changed'] == '0'
    assert result.summary['confirmed_events'] == '0' and result.summary['plan_findings'] == '0'
    assert any(i['code'] == 'PLAN_MATCH_AMBIGUOUS' for i in result.items[1]['issues'])
    assert all(not f['confirmed'] for item in result.items for f in item['findings'])


def test_rule_report_preserves_facts_escapes_objects_and_retains_distinct_sql(snapshot, tmp_path):
    result, queries, snapshots, irs = summarize(snapshot, [
        (access_rows('<img src=x>'), access_rows('<img src=x>', True, ('50', '50')), []),
        (access_rows('other'), access_rows('other', True, ('500', '500')), []),
    ])
    render_reports(tmp_path, result, queries, snapshots, irs, {}, {})
    text = (tmp_path / 'report.html').read_text()
    assert '<img src=x>' not in text and '&lt;img src=x&gt;' in text
    assert 'IndexRangeScan' in text and 'TableRangeScan' in text and 'IndexReader' in text and 'TableReader' in text
    assert 'ACCESS_SCAN' in text and '节点对应依据' in text
    assert '相似差异分组' in text and '不代表自动批量通过' in text
    assert 'data-group="query-1,query-2"' in text
    assert '辅助指标 · 估算行数' in text
