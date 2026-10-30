import json
import re
import subprocess
import sys
from pathlib import Path

from pcc_tool.models import PlanIR, PlanNode, PlanSnapshot
from pcc_tool.render.navigation import navigation_markup, plan_copy_text, plan_group
from pcc_tool.render.report import plan_table


def test_full_tree_groups_ignore_objects_but_preserve_hierarchy_roles_and_engine():
    def tree(name, role='Build', engine='tikv', reverse=False):
        children = [PlanNode('1', 'IndexRangeScan', access=f'table:{name},index:i(id)', role=role, engine=engine),
                    PlanNode('2', 'TableFullScan', access=f'table:{name}', estimate='1234')]
        return PlanIR(name, [PlanNode('0', 'HashJoin', children=children[::-1] if reverse else children)])
    a, b = tree('orders'), tree('archive')
    def key(ir, status='changed'):
        return plan_group({('q','old'):ir, ('q','new'):ir}, 'q', status)[0]
    assert key(a) == key(b) != ''
    assert key(a) != key(tree('orders', role='Probe'))
    assert key(a) != key(tree('orders', engine='tiflash'))
    assert key(a) != key(tree('orders', reverse=True))
    assert key(a, 'incomparable') == ''
    assert plan_group({('q','old'):a}, 'q', 'changed')[0] == ''


def test_copy_preserves_raw_plan_columns_ids_branches_and_long_text():
    text = 'x' * 100000 + '<script>'
    snap = PlanSnapshot('q','old', columns=['id','estRows','operator info'],
                        rows=[['IndexReader_7','0',text],['└─IndexRangeScan_9','0','range:[1,4]']])
    copied = plan_copy_text(snap, None)
    assert copied == 'id\testRows\toperator info\nIndexReader_7\t0\t' + text + '\n└─IndexRangeScan_9\t0\trange:[1,4]'
    node = PlanNode('n0','IndexRangeScan',estimate='0',evidence={'raw_id':'IndexRangeScan_99','raw':{'operator info':'keep order:true'}})
    assert 'IndexRangeScan_99\t0\t\t\tkeep order:true' in plan_copy_text(None, PlanIR('q',[node]))
    assert plan_copy_text(None,None) == ''
    node.evidence['raw'] = {'operatorInfo':'table.column->Column#91', 'accessObject':'table:raw', 'taskType':'root', 'futureCost':'3.14'}
    copied = plan_copy_text(None, PlanIR('q',[node]))
    assert 'root\ttable:raw\ttable.column->Column#91' in copied and '"futureCost":"3.14"' in copied


def test_thousand_sqls_mount_one_detail_and_metadata_cannot_end_script():
    records = [dict(id=f'query-{i}', html=f'<article id="query-{i}"></article>',
                    preview='</script><img src=x onerror=alert(1)>') for i in range(1,1001)]
    text = navigation_markup(records)
    assert text.count('<article ') == 1
    assert '<img' not in text
    payload = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', text, re.S).group(1)
    assert json.loads(payload) == records


def test_deep_tree_reserves_real_width_and_retains_levels():
    root = PlanNode('leaf','TableFullScan')
    for i in range(22):
        root = PlanNode(str(i),'Projection',children=[root])
    table = plan_table('q',{('q','old'):PlanIR('q',[root])},{},[],'query-1')
    assert 'data-depth="22" style="--depth:22"' in table
    assert 'min-width:1776px' in table
    assert 'aria-label="执行计划，可滚动"' in table


def test_navigation_javascript_interactions():
    # The interaction harness is independent of a browser and does not assert visual layout.
    root = Path(__file__).parents[2]
    result = subprocess.run(['node', str(root / 'tests/report_navigation.cjs'), sys.executable],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
