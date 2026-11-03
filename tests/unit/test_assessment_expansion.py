"""Fresh real plans, reversal controls and boundaries for composed rule proofs."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from pcc_tool import api
from pcc_tool.models import PlanSnapshot, QueryCase
from pcc_tool.plan.assessment import evaluate, walk
from pcc_tool.plan.assessment_cost import estimate_tradeoff, type_width
from pcc_tool.plan.assessment_proof import prove_plans
from pcc_tool.plan.rule_catalog import RULE_CATALOG
from pcc_tool.render.assessment import ASSESSMENT_SCRIPT


FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/assessment_expansion.json').read_text())
CASES = {c['name']: c for c in FIXTURE['cases']}


def captured(name, reverse=False):
    case = CASES[name]
    query = QueryCase(**copy.deepcopy(case['query']))
    snapshots = {}
    for side, raw in zip(('old', 'new'), reversed(case['plans']) if reverse else case['plans']):
        snapshot = PlanSnapshot(**copy.deepcopy(raw))
        snapshot.side = side
        snapshots[query.query_id, side] = snapshot
    result, irs = api._summarize([query], snapshots, 'expansion-controls', [], assessment_schema=FIXTURE['schema_evidence'])
    return result.items[0], query, irs[query.query_id, 'old'], irs[query.query_id, 'new'], {s: snapshots[query.query_id, s] for s in ('old', 'new')}


@pytest.mark.parametrize('name', CASES)
@pytest.mark.parametrize('reverse', [False, True])
def test_captured_expansion_and_reverse(name, reverse):
    item, *_ = captured(name, reverse)
    expected = CASES[name]['expected']
    if reverse and expected != 'equivalent' and expected != 'review':
        expected = 'worse' if expected == 'better' else 'better'
    assert item['compare_status'] == 'changed'
    assert item['assessment']['status'] == expected, item['assessment']
    if expected != 'review':
        assert not item['assessment']['uncovered']
        assert item['assessment']['matched']
    assert evaluate(item['assessment_evidence'], [r['id'] for r in RULE_CATALOG])['status'] == expected
    assert evaluate(item['assessment_evidence'], [])['status'] == 'review'


@pytest.mark.parametrize('name', ['captured-0032', 'captured-0068', 'captured-0167', 'captured-0288', 'captured-0361'])
def test_common_model_is_visible_and_requires_its_card(name):
    item, *_ = captured(name)
    matches = [m for m in item['assessment_evidence']['matches'] if m['rule_id'] == 'A22']
    assert len(matches) == 1
    assert matches[0]['basis'] == 'estimated_work'
    model = matches[0]['model']
    assert set(model['old']['units']) == set(model['weights']) == set(model['new']['units'])
    assert model['assumptions'] and model['tolerance_ratio'] == '1.2'
    assert evaluate(item['assessment_evidence'], [r['id'] for r in RULE_CATALOG if r['id'] != 'A22'])['status'] == 'review'


@pytest.mark.parametrize('name', ['captured-0041', 'captured-0115', 'captured-0332', 'captured-0396'])
def test_combination_requires_every_proof_card(name):
    item, *_ = captured(name)
    evidence = item['assessment_evidence']
    required = set().union(*(set(m.get('requires_rules', [])) for m in evidence['matches']))
    assert len(required) >= 2
    for missing in required:
        enabled = [r['id'] for r in RULE_CATALOG if r['id'] != missing]
        assert evaluate(evidence, enabled)['status'] == 'review', missing


@pytest.mark.parametrize('mutation', ['range', 'engine', 'unknown', 'computed', 'column_order'])
def test_projection_proof_cannot_swallow_unrelated_changes(mutation):
    _, query, old, new, captures = captured('captured-0021')
    nodes = [n for root in new.roots for n in walk(root)]
    projection = next(n for n in nodes if n.operator == 'Projection')
    leaf = nodes[-1]
    if mutation == 'range':
        leaf.attributes += ', changed predicate'
    elif mutation == 'engine':
        leaf.engine = 'tiflash'
    elif mutation == 'unknown':
        leaf.unknown['future'] = 'value'
    elif mutation == 'computed':
        projection.attributes = 'plus(d.t.a, 1)->Column#999'
    else:
        projection.attributes += ', d.t.unrelated'
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_unknown_version_cannot_use_source_specific_rules():
    _, query, old, new, captures = captured('captured-0021')
    captures['new'].version = 'v9.0.0'
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None
    assert estimate_tradeoff(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_long_constant_requires_complete_binding_not_just_prefix_and_length():
    _, query, old, new, captures = captured('captured-0115')
    value = query.parameters[0]['value']
    query.parameters[0]['value'] = value[:-1] + '😁'  # equal byte length; different hidden suffix
    assert len(query.parameters[0]['value'].encode()) == len(value.encode())
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_collation_encoding_is_not_generic_nul_stripping():
    _, query, old, new, captures = captured('captured-0023')
    scan = next(n for root in new.roots for n in walk(root) if n.operator == 'IndexRangeScan')
    scan.attributes = scan.attributes.replace(r'\x00C', r'\x01C')
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_lineage_rejects_changed_computed_expression():
    _, query, old, new, captures = captured('captured-0071')
    node = next(n for root in new.roots for n in walk(root) if 'cast(' in n.attributes)
    node.attributes = node.attributes.replace('decimal(10,0)', 'decimal(10,1)')
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_correlated_range_change_is_not_extra_scan_io():
    item, query, old, new, captures = captured('captured-0082')
    assert item['assessment']['status'] == 'equivalent'
    assert 'E07' in {m['rule_id'] for m in item['assessment_evidence']['matches']}
    assert estimate_tradeoff(query, old, new, FIXTURE['schema_evidence'], captures) is None
    node = next(n for root in new.roots for n in walk(root) if n.operator == 'Selection')
    node.attributes = node.attributes.replace(', 5)', ', 3)')
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_partial_statistics_warning_is_retained():
    item, *_ = captured('captured-0048')
    assert item['assessment']['status'] == 'better'
    assert any(m['rule_id'] == 'E09' and '统计' in m['reason'] for m in item['assessment_evidence']['matches'])


@pytest.mark.parametrize('change', ['stats', 'width', 'operator', 'arity', 'estimate', 'engine', 'expression'])
def test_work_model_refuses_missing_or_unsupported_evidence(change):
    _, query, old, new, captures = captured('captured-0360')
    metadata = copy.deepcopy(FIXTURE['schema_evidence'])
    table = metadata[query.schema + '.orders']
    node = new.roots[0]
    if change == 'stats':
        table['stats_ready'] = False
    elif change == 'width':
        table['columns']['note'] = 'LONGTEXT'
    elif change == 'operator':
        node.operator = 'FutureExecutor'
    elif change == 'arity':
        node.children = []
    elif change == 'estimate':
        node.estimate = 'NaN'
    elif change == 'expression':
        node.attributes = 'expensive_udf(d.t.a)->Column#999'
    else:
        node.task, node.engine = 'mpp[tiflash]', 'tiflash'
    assert estimate_tradeoff(query, old, new, metadata, captures) is None


def test_integer_display_width_is_not_storage_width():
    assert type_width('BIGINT(20)') == type_width('BIGINT') == 8
    assert type_width('INT(11) UNSIGNED') == 8
    assert type_width('LONGTEXT') is None


def test_cast_semantics_are_a_specific_blocker():
    item, *_ = captured('captured-0059')
    assert item['assessment']['status'] == 'review'
    assert any('CAST 返回类型长度改变' in reason for reason in item['assessment']['reasons'])


def test_arbitrary_sub_one_estimates_are_not_a_version_floor():
    _, query, old, new, captures = captured('captured-0346')
    for plan, value in ((old, '0.2'), (new, '0.8')):
        for root in plan.roots:
            for node in walk(root):
                if node.operator in {'IndexLookUp', 'IndexRangeScan', 'TableRowIDScan'}:
                    node.estimate = value
    assert prove_plans(query, old, new, FIXTURE['schema_evidence'], captures) is None


def test_browser_python_parity_for_all_composed_and_model_controls():
    rows = []
    selected = [r['id'] for r in RULE_CATALOG]
    for name in CASES:
        for reverse in (False, True):
            item, *_ = captured(name, reverse)
            evidence = item['assessment_evidence']
            for enabled in (selected, [], [r for r in selected if r not in {'A07', 'A22', 'E11'}]):
                rows.append(dict(evidence=evidence, selected=enabled, expected=evaluate(evidence, enabled)))
    script = ASSESSMENT_SCRIPT + '\nlet s="";process.stdin.on("data",x=>s+=x);process.stdin.on("end",()=>console.log(JSON.stringify(JSON.parse(s).map(x=>PCCAssessment.evaluate(x.evidence,x.selected,{min_rows:"1000",max_ratio_pct:"10",reduction_pct:"50",estimate_pct:"10"})))));'
    output = subprocess.run(['node', '-e', script], input=json.dumps(rows), text=True, capture_output=True, check=True)
    assert json.loads(output.stdout) == [row['expected'] for row in rows]
