"""Run the permanent SQL corpus against explicitly configured local test instances."""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot.tokens import TokenType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from case_catalog import SCHEMAS, load_cases, materialize
from pcc_tool import api
from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.models import CompareRequest, DiffRequest, ExportRequest, PrepareRequest


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def seed(profile, schemas):
    """Create only new schemas; never drop, truncate, or reuse existing objects."""
    with Database(profile) as db:
        db.version()
        for schema in schemas:
            if db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (schema,)) != "0":
                raise RuntimeError("Fixture schema already exists; use a new prefix")
        for schema in schemas:
            sql = (ROOT / "tests/fixtures/live_setup.sql").read_text().replace("pcc_validation_20261011", schema)
            start = 0
            for token in sqlglot.tokenize(sql, read="mysql"):
                if token.token_type == TokenType.SEMICOLON:
                    db.query(sql[start:token.end + 1])
                    start = token.end + 1
            db.query((ROOT / "tests/fixtures/sql_matrix_setup.sql").read_text().replace("pcc_validation_20261011", schema))
            data = {
                "customers": [(i, i % 5, f"customer-{i}") for i in range(1, 101)],
                "orders": [(i, i % 100 + 1, i * 37 % 2000, i % 4, f"item-{i} 100%") for i in range(1, 1001)],
                "items": [(i, i % 1000 + 1, i % 10, i * 13 % 500) for i in range(1, 2001)],
                "partitioned_orders": [(i, i * 11 % 400) for i in range(240)],
            }
            for table, rows in data.items():
                with db.connection.cursor() as cur:
                    cur.executemany(f"INSERT INTO `{schema}`.`{table}` VALUES ({','.join(['%s'] * len(rows[0]))})", rows)
                db.query(f"ANALYZE TABLE `{schema}`.`{table}`")
            columns = "id,tiny,small,medium,signed_int,signed_big,unsigned_big,f,d,money,precise_value,bits,flag,fixed_char,text_value,ci_value,binary_value,varbinary_value,tiny_text,text_body,medium_text,long_text,tiny_blob,blob_value,medium_blob,long_blob,day_value,dt,ts,duration,yr,enum_value,set_value,doc,nullable_value"
            for i in range(32):
                values = [i,i-16,i*100,i*1000,i,i*1000000,2**63+i,1.25,1.125,
                          f"{i}.123456","123456789012345678901234567890.123456789012345678901234567890",
                          i,i%2,"fixed",f"中文😀'?,;\\%s-{i}",f"Case-{i}",b"a\x00b",b"\xff\x00",
                          "tiny","body","medium","long",b"tiny",b"\xff\x00",b"medium",b"long",
                          "2024-02-29","2024-02-29 12:34:56.123456","2024-02-29 12:34:56.123456",
                          "-12:34:56.123456",2024,"paid","a,c",json.dumps({"n":i,"s":"?,;中文"}),None if i%2 else i]
                db.query(f"INSERT INTO `{schema}`.matrix_types ({columns}) VALUES ({','.join(['%s']*len(values))})", tuple(values))
            db.query(f"ANALYZE TABLE `{schema}`.matrix_types")
            print(f"Created synthetic fixtures: {schema}", flush=True)


def counts(profiles, schemas):
    result = {}
    for side, profile in profiles.items():
        with Database(profile) as db:
            result[side] = {}
            for schema in schemas:
                _, rows = db.query("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'", (schema,))
                for row in rows:
                    table = row[0]
                    result[side][f"{schema}.{table}"] = db.scalar(f"SELECT COUNT(*) FROM `{schema}`.`{table}`")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "compare"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--schema-prefix", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report-name", default="report", help="New comparison directory; reuse the original bundle and imports")
    args = parser.parse_args()
    if not re.fullmatch(r"pcc_[a-z0-9_]{1,40}", args.schema_prefix):
        raise ValueError("Use a new pcc_ prefixed fixture name")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.report_name):
        raise ValueError("Use a simple report directory name")
    profiles = load_profiles(args.config)
    profiles = {name: profiles[name] for name in ("prod", "old", "new")}
    for p in profiles.values():
        if p["host"] not in {"127.0.0.1", "localhost", "::1"} or not p.get("expected_version"):
            raise ValueError("This fixture runner only permits local instances with explicit versions")
    schemas = {name: args.schema_prefix + "_" + name.removeprefix("pcc_case_") for name in SCHEMAS}
    out = Path(args.out).resolve()
    cases = load_cases()
    if args.phase == "prepare":
        out.mkdir(parents=True, exist_ok=False)
        versions = {}
        for side, profile in profiles.items():
            with Database(profile) as db:
                versions[side] = db.version()
                for schema in schemas.values():
                    assert db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (schema,)) == "0", "Use fresh databases on all three instances"
        save(out / "versions.json", versions)
        seed(profiles["prod"], schemas.values())
        rows = [materialize(c, schemas) for c in cases]
        for row in rows:
            row["source_cluster"] = row["origin"]["source_cluster"]
        (out / "samples.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        bundle = api.export(ExportRequest(str(out / "bundle"), source="prod", sql_file=str(out / "samples.jsonl"),
                                         statement_types=("select", "update", "delete", "insert", "replace")), profiles=profiles)
        assert len(bundle.queries) == len(cases), "Cases must retain distinct identities"
        save(out / "case-index.json", [dict(case_id=c["id"], name=c["name"], category=c["category"], query_id=q.query_id,
                                            expected_guard=c["expected_guard"]) for c, q in zip(cases, bundle.queries)])
        api.prepare(PrepareRequest(bundle.path, ("old", "new"), str(out / "prepared")), profiles=profiles)
        print("Prepared both imports. Import each generated import_commands.sql before the compare phase.", flush=True)
        return
    before = counts(profiles, schemas.values())
    result = api.compare(CompareRequest(str(out / "bundle"), "old", "new", str(out / args.report_name),
                                        concurrency=4, stats_retries=5, timeout_seconds=60), profiles=profiles)
    after = counts(profiles, schemas.values())
    assert before == after, "EXPLAIN must not change fixture row counts"
    offline = api.diff(DiffRequest(result.output_dir, str(out / (args.report_name + "-offline"))))
    assert result.summary == offline.summary and result.items == offline.items and result.diffs == offline.diffs
    index = json.loads((out / "case-index.json").read_text())
    by_id = {item["query_id"]: item for item in result.items}
    checks = [{**case, "compare_status":by_id[case["query_id"]]["compare_status"],
               "issues":sorted({i["code"] for i in by_id[case["query_id"]].get("issues", [])})} for case in index]
    incomplete = [c for c in checks if c["expected_guard"] == "ok" and c["compare_status"] not in {"same", "changed"}]
    save(out / (args.report_name + "-case-results.json"), checks)
    save(out / (args.report_name + "-validation.json"), dict(summary=result.summary, row_counts_unchanged=True, offline_replay_equal=True,
              all_executable_cases_compared=not incomplete, unresolved_cases=incomplete,
              categories=dict(Counter(c["category"] for c in cases)), row_counts=after))
    print(json.dumps(result.summary, ensure_ascii=False), flush=True)
    print(f"Report: {out / args.report_name / 'report.html'}", flush=True)
    if incomplete:
        print(f"{len(incomplete)} executable cases remain incomplete; retained in the report and validation evidence.", flush=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
