"""Real TiDB rule controls using NTH_PLAN; separate from upgrade comparisons.

Only localhost test profiles are accepted. --create-fixture creates new schemas;
without it, the existing, explicitly named synthetic fixture is read only.
"""
import argparse
import copy
import json
import re
from pathlib import Path

from pcc_tool import api
from pcc_tool.artifacts.store import finalize, write_json
from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.models import CaptureBundle, DiffRequest, ExportRequest, PlanSnapshot, QueryCase, VERSIONS, digest, primitive
from pcc_tool.plan.assessment import schema_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--create-fixture', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'pcc_rules_[a-z0-9_]+', args.schema):
        parser.error('Use a dedicated pcc_rules_ schema')
    profiles = load_profiles(args.config)
    targets = {side: profiles[side] for side in ('old', 'new')}
    expected_versions = {'old': 'v6.5.12', 'new': 'v8.5.5'}
    for side, profile in targets.items():
        if profile.get('role') != 'test' or profile['host'] not in {'localhost', '127.0.0.1', '::1'}:
            parser.error('Only explicitly isolated localhost test profiles are accepted')
        with Database(profile) as db:
            assert db.version() == expected_versions[side]
            exists = db.scalar('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (args.schema,))
            assert exists == ('0' if args.create_fixture else '1'), 'Use a fresh schema for creation'
    args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
    table = f'`{args.schema}`.`rule_input`'
    if args.create_fixture:
        for side, profile in targets.items():
            with Database(profile) as db:
                db.query(f'CREATE DATABASE `{args.schema}`')
                db.query(f'CREATE TABLE {table} (id BIGINT PRIMARY KEY CLUSTERED, value DOUBLE NOT NULL)')
                with db.connection.cursor() as cursor:
                    cursor.executemany(f'INSERT INTO {table} VALUES (%s,%s)', [(i, i % 257) for i in range(1, 20001)])
                db.query(f'ANALYZE TABLE {table}')
                print(side, expected_versions[side], 'created 20000 synthetic rows', flush=True)
    sqls = {
        'count': 'SELECT /*+ HASH_AGG(), USE_INDEX(rule_input) */ COUNT(*) FROM rule_input',
        'sum': 'SELECT /*+ HASH_AGG(), USE_INDEX(rule_input) */ SUM(value) FROM rule_input',
        'small_count': 'SELECT /*+ HASH_AGG(), USE_INDEX(rule_input) */ COUNT(*) FROM rule_input WHERE id <= 5',
    }
    samples = args.out / 'samples.jsonl'
    samples.write_text(''.join(json.dumps({'schema': args.schema, 'sql_text': sql}) + '\n' for sql in sqls.values()))
    queries, snapshots, checks, fixtures, metadata = [], {}, [], [], {}
    for target, profile in targets.items():
        exported = api.export(ExportRequest(str(args.out / (target + '-bundle')), source=target, sql_file=str(samples)), profiles=profiles)
        assert not exported.issues, exported.issues
        schema = schema_evidence(exported)
        assert schema[args.schema + '.rule_input']['stats_ready']
        metadata.update(schema)
        captured = {}
        with Database(profile) as db:
            version = db.version()
            db.query(f'USE `{args.schema}`')
            before = db.scalar(f'SELECT COUNT(*) FROM {table}')
            assert before == '20000'
            session = db.variables()
            for name, base_sql in sqls.items():
                for nth in (1, 2):
                    sql = base_sql.replace('HASH_AGG()', f'HASH_AGG(), NTH_PLAN({nth})')
                    columns, rows = db.query("EXPLAIN FORMAT='row' " + sql)
                    warnings = db.records('SHOW WARNINGS')
                    assert not warnings, 'The control hint did not select an available plan'
                    captured[name, nth] = dict(columns=columns, rows=rows, version=version,
                        context={'session': session, 'capture_sql': sql, 'validation_kind': 'controlled_hint_comparison'})
            assert db.scalar(f'SELECT COUNT(*) FROM {table}') == before
        cases = [('count_push', 'count', 2, 1, 'better'), ('count_reverse', 'count', 1, 2, 'worse'),
                 ('sum_push', 'sum', 2, 1, 'better'), ('sum_reverse', 'sum', 1, 2, 'worse'),
                 ('small_input', 'small_count', 2, 1, 'review'), ('same_plan', 'count', 1, 1, 'excluded')]
        for name, sql_name, left, right, expected in cases:
            label = version + ':' + name
            query = QueryCase.create(sqls[sql_name], args.schema, context={'validation_case': label},
                                     dependencies=[[args.schema, 'rule_input']])
            queries.append(query)
            for side, nth in [('old', left), ('new', right)]:
                snapshots[query.query_id, side] = PlanSnapshot(query.query_id, side, **copy.deepcopy(captured[sql_name, nth]))
            fixtures.append(dict(name=label, query=primitive(query), schema_evidence=schema, expected=expected,
                                 plans=[primitive(snapshots[query.query_id, side]) for side in ('old', 'new')]))
            checks.append(dict(name=label, query_id=query.query_id, expected=expected))
    note = '规则定向验收：使用 NTH_PLAN 在同一 TiDB 版本中选择合法计划，包含正反对照；不是自然版本升级收益，也没有测量耗时。'
    bundle = CaptureBundle({'objects': []}, queries, context={'validation_note': note, 'validation_kind': 'controlled_hint_comparison'})
    result, irs = api._summarize(queries, snapshots, digest(fixtures), [], assessment_schema=metadata)
    report = args.out / 'report'
    api._write_result(report, result, bundle, snapshots, irs, api.Events(), metadata)
    finalize(report, {'kind': 'ComparisonResult', 'versions': VERSIONS, 'run_status': result.run_status, 'fingerprint': result.fingerprint})
    replay = api.diff(DiffRequest(str(report), str(args.out / 'offline')))
    assert result.items == replay.items and result.summary == replay.summary
    by_id = {item['query_id']: item for item in result.items}
    for check in checks:
        item = by_id[check['query_id']]
        check.update(actual=item['assessment']['status'], compare_status=item['compare_status'])
    write_json(args.out / 'captured-controls.json', {'kind': 'controlled_hint_comparison', 'note': note, 'cases': fixtures})
    write_json(args.out / 'validation.json', {'note': note, 'checks': checks, 'summary': result.summary,
        'row_counts_unchanged': True, 'offline_equal': True, 'uses_real_explain_rows': True})
    for check in checks:
        print(check['name'], check['expected'], check['actual'], flush=True)
    assert all(check['actual'] == check['expected'] for check in checks), 'Rule controls failed; see archived raw plans'
    print('Report:', report / 'report.html')


if __name__ == '__main__':
    main()
