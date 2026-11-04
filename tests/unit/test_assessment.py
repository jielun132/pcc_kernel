"""Decisions must have positive evidence, including inverse and refusal cases."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from pcc_tool import api
from pcc_tool.models import CaptureBundle, DiffRequest, PlanSnapshot, QueryCase, VERSIONS
from pcc_tool.plan.assessment import detect, evaluate, guard_pass, schema_evidence
from pcc_tool.plan.rule_catalog import DEFAULT_RULES, DEFAULT_THRESHOLDS, RULE_CATALOG
from pcc_tool.render.assessment import ASSESSMENT_SCRIPT, assessment_markup


META = {"d.t": {"columns": {"id": "BIGINT", "a": "INT", "b": "INT"}, "primary_key": ["id"], "clustered_pk": True, "stats_ready": True}}
CAPTURED_CONTROLS = json.loads((Path(__file__).parents[1] / 'fixtures/assessment_controls.json').read_text())['cases']


@pytest.mark.parametrize('case', CAPTURED_CONTROLS, ids=lambda case: case['name'])
def test_real_tidb_plan_controls(case):
    query = QueryCase(**case['query'])
    snapshots = {(query.query_id, plan['side']): PlanSnapshot(**plan) for plan in case['plans']}
    result, _ = api._summarize([query], snapshots, 'real-tidb-controls', [], assessment_schema=case['schema_evidence'])
    item = result.items[0]
    assert item['assessment']['status'] == case['expected'], item['assessment_evidence']
    if case['expected'] in {'better', 'worse'}:
        assert {match['rule_id'] for match in item['assessment_evidence']['matches']} == {'A12'}
    elif case['expected'] == 'excluded':
        assert item['compare_status'] == 'same'


def node(op, *children, est="10000", task="root", access="", info="", role=""):
    return dict(op=op, children=children, est=est, task=task, access=access, info=info, role=role)


def scan(**kw):
    return node("TableFullScan", task="cop[tikv]", access="table:t", info="keep order:false", **kw)


def reader(child=None, est="10000"):
    return node("TableReader", child or scan(), est=est)


def rows(root):
    result = []
    def visit(n, depth):
        index = len(result)
        name = n['op'] + '_' + str(index)
        info = n['info']
        if n['op'] in {'TableReader', 'IndexReader'} and not info:
            info = ('data:' if n['op'] == 'TableReader' else 'index:') + n['children'][0]['op'] + '_' + str(index + 1)
        result.append(['  ' * depth + name + (f"({n['role']})" if n['role'] else ''), n['est'], n['task'], n['access'], info])
        for child in n['children']:
            visit(child, depth + 1)
    visit(root, 0)
    return result


def assess(snapshot, a, b, *, sql="SELECT id FROM t", meta=META):
    query = QueryCase.create(sql, "d")
    snapshots = {(query.query_id, side): snapshot(rows(plan), side=side, query_id=query.query_id) for side, plan in [('old', a), ('new', b)]}
    result, irs = api._summarize([query], snapshots, 'assessment-test', [], assessment_schema=meta)
    assert result.items[0]['compare_status'] == 'changed'
    return result.items[0], result, query, snapshots, irs


def directional_cases():
    predicate = 'lt(d.t.id, 100)'
    yield 'A06', node('Selection', reader(), est='100', info=predicate), reader(node('Selection', scan(), est='100', task='cop[tikv]', info=predicate), '100'), 'SELECT id FROM t WHERE id < 100'
    for op, key in [('TopN', 'A08'), ('Limit', 'A09')]:
        info = ('d.t.id, ' if op == 'TopN' else '') + 'offset:2, count:10'
        pushed = ('d.t.id, ' if op == 'TopN' else '') + 'offset:0, count:12'
        yield key, node(op, reader(), est='10', info=info), node(op, reader(node(op, scan(), est='12', task='cop[tikv]', info=pushed), '12'), est='10', info=info), 'SELECT id FROM t LIMIT 2,10'
    yield 'A11', node('Limit', node('Sort', reader(), info='d.t.id'), est='10', info='offset:0, count:10'), node('TopN', reader(), est='10', info='d.t.id, offset:0, count:10'), 'SELECT id FROM t ORDER BY id LIMIT 10'
    yield 'A12', node('HashAgg', reader(), est='1', info='funcs:count(1)->Column#2'), node('HashAgg', reader(node('HashAgg', scan(), est='1', task='cop[tikv]', info='funcs:count(1)->Column#3'), '1'), est='1', info='funcs:count(Column#3)->Column#2'), 'SELECT COUNT(*) FROM t'
    yield 'A17', node('Selection', reader(), est='0', info='eq(1, 0)'), node('TableDual', est='0', info='rows:0'), 'SELECT id FROM t WHERE 1=0'
    yield 'A01', reader(node('TableRangeScan', est='1', task='cop[tikv]', access='table:t', info='range:[7,7], keep order:false'), '1'), node('Point_Get', est='1', access='table:t', info='handle:7'), 'SELECT id FROM t WHERE id=7'
    index = node('IndexRangeScan', est='10', task='cop[tikv]', access='table:t, index:idx_a(a)', info='range:[1,10], keep order:false', role='Build')
    table = node('TableRowIDScan', est='10', task='cop[tikv]', access='table:t', info='keep order:false', role='Probe')
    yield 'A02', node('IndexLookUp', index, table, est='10'), node('IndexReader', {**index, 'role': ''}, est='10'), 'SELECT id FROM t WHERE a BETWEEN 1 AND 10'
    ix = {**index, 'est': '10000'}
    tab = {**table, 'est': '10000', 'role': ''}
    yield 'A03', node('IndexLookUp', ix, node('Selection', tab, est='10', task='cop[tikv]', info='lt(d.t.a, 10)', role='Probe'), est='10'), node('IndexLookUp', node('Selection', {**ix, 'role': ''}, est='10', task='cop[tikv]', info='lt(d.t.a, 10)', role='Build'), table, est='10'), 'SELECT id FROM t WHERE a < 10'


@pytest.mark.parametrize('rule,a,b,sql', list(directional_cases()), ids=[x[0] for x in directional_cases()])
@pytest.mark.parametrize('reverse', [False, True], ids=['better', 'worse'])
def test_proven_direction_and_reverse(snapshot, rule, a, b, sql, reverse):
    item, *_ = assess(snapshot, b if reverse else a, a if reverse else b, sql=sql)
    assert item['assessment']['status'] == ('worse' if reverse else 'better'), item['assessment_evidence']
    assert {m['rule_id'] for m in item['assessment_evidence']['matches']} == {rule}
    assert not item['assessment']['uncovered']
    assert evaluate(item['assessment_evidence'], [])['status'] == 'review'


def test_alias_cannot_hide_other_changes_or_computation(snapshot):
    a = node('Projection', reader(), info='d.t.id')
    b = node('Projection', reader(), info='d.t.id->Column#5')
    item, *_ = assess(snapshot, a, b)
    assert item['assessment']['status'] == 'equivalent'
    b['children'] = (reader(node('TableRangeScan', task='cop[tikv]', access='table:t', info='range:[1,10], keep order:false')),)
    item, *_ = assess(snapshot, a, b)
    assert item['assessment']['status'] == 'review' and item['assessment']['uncovered']
    b['info'] = 'cast(d.t.id, int)->Column#5'
    item, *_ = assess(snapshot, a, b)
    assert not any(m['rule_id'] == 'E01' for m in item['assessment_evidence']['matches'])


def test_column_id_changes_without_explicit_lineage_are_not_equivalent(snapshot):
    item, *_ = assess(snapshot, node('Projection', reader(), info='Column#2'), node('Projection', reader(), info='Column#8'))
    assert item['assessment']['status'] == 'review'


@pytest.mark.parametrize('reverse', [False, True])
def test_redundant_projection_same_boundary_only(snapshot, reverse):
    b = node('Projection', reader(), info='d.t.id, d.t.a')
    a = node('Projection', b, info='d.t.id, d.t.a')
    item, *_ = assess(snapshot, b if reverse else a, a if reverse else b)
    assert item['assessment']['status'] == 'equivalent'
    assert {m['rule_id'] for m in item['assessment_evidence']['matches']} == {'E02'}
    a['info'] = 'd.t.a, d.t.id'
    item, *_ = assess(snapshot, a, b)
    assert item['assessment']['status'] == 'review'


def test_projection_pushdown_not_inferred_as_equivalent_or_better(snapshot):
    a = node('Projection', reader(), info='d.t.id')
    b = reader(node('Projection', scan(), info='d.t.id', task='cop[tikv]'))
    item, *_ = assess(snapshot, a, b)
    assert item['assessment']['status'] == 'review'
    assert not item['assessment_evidence']['matches']


def test_only_estimates_requires_opt_in_and_positive_values(snapshot):
    for before, after, expected in [('100', '110', 'equivalent'), ('100', '111', 'review'), ('0', '1', 'review')]:
        a, b = reader(scan(est=before), before), reader(scan(est=after), after)
        item, *_ = assess(snapshot, a, b)
        assert item['assessment']['status'] == 'review'
        assert evaluate(item['assessment_evidence'], ['E05'])['status'] == expected


def test_small_or_untrusted_scan_not_automatic(snapshot):
    _, a, b, sql = next(directional_cases())
    item, *_ = assess(snapshot, a, b, sql=sql, meta={})
    assert item['assessment']['status'] == 'review'
    item, *_ = assess(snapshot, a, b, sql=sql)
    assert evaluate(item['assessment_evidence'], thresholds={'min_rows': '10001'})['status'] == 'review'
    assert evaluate(item['assessment_evidence'], thresholds={'reduction_pct': '99'})['status'] == 'better'
    a['children'][0]['children'][0]['info'] += ', stats:pseudo'
    b['children'][0]['children'][0]['info'] += ', stats:pseudo'
    item, *_ = assess(snapshot, a, b, sql=sql)
    assert item['assessment']['status'] == 'review'


def test_topn_offset_order_extra_reader_fields_refuse(snapshot):
    _, a, b, sql = list(directional_cases())[1]
    for modification in ['offset', 'order', 'reader', 'access', 'role']:
        changed = copy.deepcopy(b)
        if modification == 'offset':
            changed['children'][0]['children'][0]['info'] = 'd.t.id, offset:2, count:10'
        elif modification == 'order':
            changed['children'][0]['children'][0]['info'] = 'd.t.a, offset:0, count:12'
        elif modification == 'reader':
            changed['children'][0]['info'] = 'data:TopN_2, partition:p1'
        elif modification == 'access':
            changed['children'][0]['children'][0]['access'] = 'table:unexpected'
        else:
            changed['children'][0]['children'][0]['role'] = 'Build'
        item, *_ = assess(snapshot, a, changed, sql=sql)
        assert item['assessment']['status'] == 'review'


@pytest.mark.parametrize('predicate', ['lt(rand(), 1)', "eq(d.t.id, '100')", 'eq(cast(d.t.id, int), 100)', 'eq(Column#7, 100)'])
def test_pushdown_unknown_expression_or_type_refuses(snapshot, predicate):
    _, a, b, sql = next(directional_cases())
    a['info'] = b['children'][0]['info'] = predicate
    item, *_ = assess(snapshot, a, b, sql=sql)
    assert item['assessment']['status'] == 'review'


def test_coverage_blockers_and_conflicts():
    e = dict(scope='changed', event_refs=['1', '2'], matches=[
        dict(id='m1', rule_id='A06', direction='better', event_refs=['1'], guards=[]),
        dict(id='m2', rule_id='A08', direction='worse', event_refs=['2'], guards=[])], blockers=[])
    assert evaluate(e)['status'] == 'review'
    assert '同时命中变优和变差，不抵消' in evaluate(e)['reasons']
    e['matches'][1]['direction'] = 'equivalent'
    assert evaluate(e)['status'] == 'better'
    e['blockers'] = ['语义不完整']
    assert evaluate(e)['status'] == 'review'
    e['scope'] = 'incomparable'
    assert evaluate(e)['status'] == 'excluded'


def test_count_final_mode_is_not_a_sql_sum_rewrite(snapshot):
    _, a, b, sql = next(c for c in directional_cases() if c[0] == 'A12')
    b['info'] = 'funcs:sum(Column#3)->Column#2'
    item, *_ = assess(snapshot, a, b, sql=sql)
    assert item['assessment']['status'] == 'review'


def test_false_estimate_is_not_constant_false_proof(snapshot):
    _, a, b, _ = next(c for c in directional_cases() if c[0] == 'A17')
    item, *_ = assess(snapshot, a, b, sql='SELECT id FROM t WHERE id=999')
    assert item['assessment']['status'] == 'review'


def test_unique_key_and_covering_require_schema_proof(snapshot):
    for key in ['A01', 'A02']:
        _, a, b, sql = next(c for c in directional_cases() if c[0] == key)
        metadata = copy.deepcopy(META)
        metadata['d.t']['clustered_pk'] = False
        item, *_ = assess(snapshot, a, b, sql=sql, meta=metadata)
        assert item['assessment']['status'] == 'review'
    _, a, b, _ = next(c for c in directional_cases() if c[0] == 'A02')
    item, *_ = assess(snapshot, a, b, sql='SELECT b FROM t WHERE a BETWEEN 1 AND 10')
    assert item['assessment']['status'] == 'review'


def test_predicate_whitelist_refuses_strings_null_casts(snapshot):
    a = node('Selection', reader(), est='1', info='eq(1, 2), lt(3, 4)')
    b = node('Selection', reader(), est='1', info='gt(4, 3), eq(2, 1)')
    item, *_ = assess(snapshot, a, b)
    assert item['assessment']['status'] == 'equivalent'
    for text in ["eq('1', 2)", 'eq(NULL, 2)', 'eq(cast(1, int), 2)']:
        b['info'] = text
        item, *_ = assess(snapshot, a, b)
        assert item['assessment']['status'] == 'review'


def test_schema_evidence_and_old_archive_fallback(tmp_path):
    (tmp_path / 't.sql').write_text('CREATE TABLE t(id BIGINT PRIMARY KEY /*T![clustered_index] CLUSTERED */, a INT);')
    bundle = CaptureBundle({'objects': [{'schema': 'd', 'table': 't', 'ddl_file': 't.sql', 'stats_ready': True}]}, [], path=str(tmp_path))
    metadata = schema_evidence(bundle)['d.t']
    assert metadata['primary_key'] == ['id'] and metadata['stats_ready']
    bundle.path = ''
    assert schema_evidence(bundle) == {}


def test_evidence_archive_roundtrip_without_database(snapshot, tmp_path, monkeypatch):
    from pcc_tool.artifacts.store import finalize, read_jsonl, read_json, verify
    _, a, b, sql = next(directional_cases())
    item, result, query, snapshots, irs = assess(snapshot, a, b, sql=sql)
    source = tmp_path / 'source'
    api._write_result(source, result, CaptureBundle({'objects': []}, [query]), snapshots, irs, api.Events(), META)
    finalize(source, {'kind': 'ComparisonResult', 'versions': VERSIONS})
    def forbid(*args, **kwargs):
        raise AssertionError('Offline assessment must not connect to a database')
    monkeypatch.setattr(api, 'Database', forbid)
    replay = api.diff(DiffRequest(str(source), str(tmp_path / 'offline')))
    assert replay.items == result.items and replay.summary == result.summary
    assert read_json(tmp_path / 'offline/inputs/assessment_schema.json') == META
    assert read_jsonl(tmp_path / 'offline/assessments.jsonl') == read_jsonl(source / 'assessments.jsonl')
    assert 'assessment_rules.json' in verify(tmp_path / 'offline')['files']


def test_assessment_config_cannot_close_script():
    value = '</script><img src=x onerror=alert(1)>'
    markup = assessment_markup(value)
    assert value not in markup and '\\u003c/script' in markup
    assert markup.count('data-rule-id=') == len(RULE_CATALOG)


def test_catalog_ids_defaults_and_supported_detectors():
    assert len(RULE_CATALOG) == len({r['id'] for r in RULE_CATALOG}) == 64
    hard = {r['id'] for r in RULE_CATALOG if r['group'] == 'data_review'}
    optional = {r['id'] for r in RULE_CATALOG if r['group'] == 'runtime_note'}
    assert hard == {'D01', 'D02', 'D08', 'D10'} and optional == {f'D{i:02d}' for i in range(1, 14)} - hard
    assert hard <= set(DEFAULT_RULES) and not optional.intersection(DEFAULT_RULES)
    assert 'E05' not in DEFAULT_RULES
    assert all(not r['automatic'] for r in RULE_CATALOG if r['id'].startswith('C'))
    assert {r['id'] for r in RULE_CATALOG if r['automatic']} == {x[0] for x in directional_cases()} | {
        'A07', 'A10', 'A20', 'A21', 'E01', 'E02', 'E03', 'E05', 'E06', 'E07', 'E08', 'E09', 'E10', 'E11', 'E12', 'E14', 'E15', 'E16', 'E17'}


def upgrade_pair(snapshot, a, b, sql, *, version="v7.1.9-0.3", reverse=False, meta=META):
    versions = {"old": "v6.5.12", "new": version}
    if reverse:
        a, b = b, a
        versions["new"], versions["old"] = versions["old"], versions["new"]
    def with_version(rows, side, query_id):
        return snapshot(rows, side=side, query_id=query_id, version=versions[side])
    return assess(with_version, a, b, sql=sql, meta=meta)[0]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("version", ["v7.1.9-0.3", "v8.5.7"])
def test_numeric_range_estimate_and_transparent_reader(snapshot, version, reverse):
    def path(est):
        return reader(node("TableRangeScan", est=est, task="cop[tikv]", access="table:t",
                           info="range:(10,+inf], keep order:false"), est)
    item = upgrade_pair(snapshot, path("180"), path("182"), "SELECT * FROM t WHERE id>10", version=version, reverse=reverse)
    assert evaluate(item["assessment_evidence"], ["E16"])["status"] == "equivalent"


@pytest.mark.parametrize("change", ["range", "order", "rows", "stats", "version", "type", "binding", "propagation"])
def test_static_range_proof_refuses_changed_premises(snapshot, change):
    info = "range:(10,+inf], keep order:false"
    a = reader(node("TableRangeScan", est="180", task="cop[tikv]", access="table:t", info=info), "180")
    b = copy.deepcopy(a)
    b["est"] = b["children"][0]["est"] = "182"
    meta = copy.deepcopy(META)
    leaf = b["children"][0]
    if change == "range":
        leaf["info"] = info.replace("10", "11")
    elif change == "order":
        leaf["info"] = info.replace("false", "true")
    elif change == "rows":
        b["est"] = leaf["est"] = "250"
    elif change == "stats":
        meta["d.t"]["stats_ready"] = False
    elif change == "type":
        meta["d.t"]["columns"]["id"] = "VARCHAR(32)"
    elif change == "binding":
        a["children"][0]["info"] = leaf["info"] = "range: decided by [eq(d.t.id, d.u.id)], keep order:false"
    elif change == "propagation":
        b["est"] = "170"
    version = "v9.0.0" if change == "version" else "v7.1.9-0.3"
    item = upgrade_pair(snapshot, a, b, "SELECT * FROM t WHERE id>10", version=version, meta=meta)
    assert evaluate(item["assessment_evidence"], ["E16"])["status"] == "review"


@pytest.mark.parametrize("mutation", ["", "predicate", "input", "stats", "volatile"])
def test_integer_filter_output_is_not_scan_work(snapshot, mutation):
    a = reader(node("Selection", scan(), est="0", task="cop[tikv]", info="eq(d.t.a, 1234)"), "0")
    b = copy.deepcopy(a)
    filt = b["children"][0]
    b["est"] = filt["est"] = "2"
    if mutation == "predicate":
        filt["info"] = "eq(d.t.a, 999)"
    elif mutation == "input":
        filt["children"][0]["est"] = "20000"
    elif mutation == "stats":
        filt["children"][0]["info"] += ", stats:pseudo"
    elif mutation == "volatile":
        a["children"][0]["info"] = filt["info"] = "eq(rand(), 1)"
    item = upgrade_pair(snapshot, a, b, "SELECT id FROM t WHERE a=1234")
    assert evaluate(item["assessment_evidence"], ["E16"])["status"] == ("review" if mutation else "equivalent")


@pytest.mark.parametrize("mutation", ["", "one_row", "global", "expression"])
def test_zero_input_grouped_aggregate(snapshot, mutation):
    source = node("TableDual", est="0", info="rows:0")
    info = "group by:d.t.a, funcs:firstrow(d.t.a)->d.t.a"
    if mutation == "global":
        info = "funcs:count(1)->Column#4"
    a = node("HashAgg", source, est="1", info=info)
    b = copy.deepcopy(a)
    b["op"] = "StreamAgg"
    b["children"][0]["est"] = "1"
    if mutation == "one_row":
        b["children"][0]["info"] = "rows:1"
    elif mutation == "expression":
        b["info"] = info.replace("firstrow", "max")
    item = upgrade_pair(snapshot, a, b, "SELECT DISTINCT a FROM t WHERE id>20 AND id<10")
    assert evaluate(item["assessment_evidence"], ["E11", "E17"])["status"] == ("review" if mutation else "equivalent")


@pytest.mark.parametrize("mutation", ["", "frame", "rank", "input", "sort", "stats"])
def test_ordered_window_estimates_require_identical_ordered_row_number(snapshot, mutation):
    index = node("IndexFullScan", est="100", task="cop[tikv]", access="table:t, index:idx_a(a)", info="keep order:true")
    info = "row_number()->Column#7 over(partition by d.t.a order by d.t.id rows between current row and current row)"
    a = node("Projection", node("Window", node("IndexReader", index, est="100"), est="100", info=info),
             est="100", info="d.t.id, Column#7")
    b = copy.deepcopy(a)
    win = b["children"][0]
    win["op"] = "OrderedWindow"
    leaf = win["children"][0]["children"][0]
    if mutation == "frame":
        win["info"] = info.replace("current row", "unbounded preceding", 1)
    elif mutation == "rank":
        a["children"][0]["info"] = win["info"] = info.replace("row_number()", "rank()")
    elif mutation == "input":
        leaf["info"] = "keep order:false"
    elif mutation == "sort":
        win["children"] = (node("Sort", win["children"][0], info="d.t.id", est="100"),)
    elif mutation == "stats":
        leaf["info"] += ", stats:partial[a:unInitialized]"
    item = upgrade_pair(snapshot, a, b, "SELECT id, ROW_NUMBER() OVER (PARTITION BY a ORDER BY id) FROM t")
    assert evaluate(item["assessment_evidence"], ["A22"])["status"] == "review"
    cards = {r["rule_id"] for r in item["assessment_evidence"]["mandatory_reviews"]}
    assert "D09" in {r["rule_id"] for r in item["assessment_evidence"]["runtime_notes"]}
    assert ("D08" in cards) == (not mutation)
    assert any(m["rule_id"] == "A22" for m in item["assessment_evidence"]["matches"]) == (not mutation)
    if not mutation:
        model = next(m["model"] for m in item["assessment_evidence"]["matches"] if m["rule_id"] == "A22")
        assert model["old_score"] == model["new_score"] and any("缓冲" in s for s in model["new"]["assumptions"])


@pytest.mark.parametrize("mutation", ["", "offset", "sort_key", "range", "prefix_type", "untrusted"])
def test_ordered_index_limit_accounts_for_merging_ranges(snapshot, mutation):
    spans = "range:[1,1], [5,5], [20,20], keep order:"
    if mutation == "range":
        spans = spans.replace("[5,5]", "[4,5]")
    def index(ordered):
        return node("IndexRangeScan", est="30", task="cop[tikv]", access="table:t, index:ia(a)",
                    info=spans + ("true" if ordered else "false"))
    key = "a" if mutation == "sort_key" else "id"
    a = node("TopN", node("IndexReader", index(False), est="30"), info=f"d.t.{key}, offset:100, count:10", est="10")
    local = node("Limit", index(True), est="30", task="cop[tikv]", info="offset:0, count:" + ("111" if mutation == "offset" else "110"))
    b = node("Limit", node("IndexReader", local, est="30"), est="10", info="offset:100, count:10")
    meta = copy.deepcopy(META)
    if mutation == "prefix_type":
        meta["d.t"]["columns"]["a"] = "VARCHAR(18)"
    elif mutation == "untrusted":
        meta["d.t"]["stats_ready"] = False
    sql = f"SELECT id,a FROM t WHERE a IN (1,5,20) ORDER BY {key} LIMIT 10 OFFSET 100"
    item = upgrade_pair(snapshot, a, b, sql, meta=meta)
    matches = [m for m in item["assessment_evidence"]["matches"] if m["rule_id"] == "A22"]
    if mutation:
        assert not matches and item["assessment"]["status"] == "review"
    else:
        assert len(matches) == 1
        assert any("3 段" in text for text in matches[0]["model"]["new"]["assumptions"])
        inverse = upgrade_pair(snapshot, a, b, sql, meta=meta, reverse=True)
        rev = next(m for m in inverse["assessment_evidence"]["matches"] if m["rule_id"] == "A22")
        assert rev["model"]["old_score"] == matches[0]["model"]["new_score"]
        assert rev["model"]["new_score"] == matches[0]["model"]["old_score"]


def test_browser_and_python_decisions_match(snapshot):
    cases = []
    for _, a, b, sql in directional_cases():
        for old, new in [(a, b), (b, a)]:
            item, *_ = assess(snapshot, old, new, sql=sql)
            for selected in [[], DEFAULT_RULES]:
                for t in [DEFAULT_THRESHOLDS, {**DEFAULT_THRESHOLDS, 'min_rows': '100000'}]:
                    e = item['assessment_evidence']
                    cases.append({'e': e, 's': selected, 't': t, 'expected': evaluate(e, selected, t)})
    huge = '100000000000000000000000000000000000000000000000001'
    for after in ['110000000000000000000000000000000000000000000000001', '110000000000000000000000000000000000000000000000002', '0', '1e4']:
        guard = dict(kind='estimate', input=huge, output=after, setting='estimate_pct', label='估算')
        e = dict(scope='changed', event_refs=['1'], matches=[dict(id='m1', rule_id='E05', direction='equivalent', event_refs=['1'], guards=[guard])], blockers=[])
        cases.append({'e': e, 's': ['E05'], 't': DEFAULT_THRESHOLDS, 'expected': evaluate(e, ['E05'])})
    script = ASSESSMENT_SCRIPT + '\nconst cases=JSON.parse(require("fs").readFileSync(0,"utf8")); for(const c of cases)require("assert").deepStrictEqual(PCCAssessment.evaluate(c.e,c.s,c.t),c.expected);console.log(cases.length);'
    out = subprocess.run(['node', '-e', script], input=json.dumps(cases), capture_output=True, text=True, check=True)
    assert int(out.stdout) == len(cases)
    assert guard_pass(dict(kind='minimum', value='1000', setting='min_rows'), DEFAULT_THRESHOLDS)
