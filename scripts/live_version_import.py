"""Reviewed fixture-only import harness; the PCC API itself remains read-only.

Requires three already verified profiles and an existing source fixture. Refuses
to overwrite target schemas, copies only the approved synthetic fixture rows,
and retains every generated material and verification result.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

from pcc_tool import api
from pcc_tool.artifacts.store import write_json, write_jsonl
from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.capture.guard import quote_identifier
from pcc_tool.collector.dependencies import qualified, show_create, verify_materials
from pcc_tool.models import ExportRequest, PrepareRequest

SCHEMA = "pcc_validation_20261011"
TABLES = ("customers", "orders", "items", "partitioned_orders", "matrix_types", "drift_fixture")


def mysql_import(profile, sql):
    environment = dict(os.environ)
    environment["MYSQL_PWD"] = os.environ.get(profile.get("password_env", ""), "")
    result = subprocess.run(["mysql", "--local-infile=1", "--batch", "--host=" + profile["host"],
        "--port=" + str(profile["port"]), "--user=" + profile["user"]], env=environment,
        input=sql, text=True, capture_output=True, timeout=120)
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--approve-schema", required=True, choices=[SCHEMA])
    args = parser.parse_args()
    profiles, root = load_profiles(args.config), Path(args.out)
    targets = tuple(args.targets.split(","))
    assert args.source not in targets and len(set(targets)) == len(targets)
    root.mkdir(parents=True, exist_ok=False)
    for target in targets:
        with Database(profiles[target]) as db:
            db.version()
            assert db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (SCHEMA,)) == "0", "Target fixture schema exists; refusing overwrite"
    queries = root / "queries.jsonl"
    write_jsonl(queries, [{"schema": SCHEMA, "sql_text": f"SELECT * FROM `{name}`"}
                         for name in (*TABLES, "nested_view")])
    bundle = api.export(ExportRequest(str(root / "bundle"), source=args.source, sql_file=str(queries)), profiles=profiles)
    write_json(root / "export.json", {"status": bundle.manifest["run_status"], "issues": bundle.issues})
    assert bundle.manifest["run_status"] == "completed", bundle.issues
    api.prepare(PrepareRequest(bundle.path, targets, str(root / "prepared")), profiles=profiles)
    for target in targets:
        script = root / "prepared" / target / "import_commands.sql"
        outcome = mysql_import(profiles[target], script.read_text())
        write_json(root / f"{target}-import.json", outcome)
        assert outcome["exit_code"] == 0, outcome
        with Database(profiles[target]) as db:
            state, issues = verify_materials(db, bundle.manifest)
            counts = {name: db.scalar(f"SELECT COUNT(*) FROM {qualified(SCHEMA, name)}") for name in TABLES}
        write_json(root / f"{target}-verification.json", {"state": state, "issues": issues, "counts_before_fixture_copy": counts})
        assert set(counts.values()) == {"0"}, "Import must not copy business data"
        # Filling synthetic rows is separate from the import acceptance result.
        # It enables existing SQL/protocol tests on equivalent small fixtures.
        with Database(profiles[args.source]) as source, Database(profiles[target]) as db:
            db.query("USE " + quote_identifier(SCHEMA))
            db.query(show_create(source, SCHEMA, "pseudo_fixture"))
            for name in (*TABLES, "pseudo_fixture"):
                columns = source.records("SELECT COLUMN_NAME,DATA_TYPE,EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (SCHEMA, name))
                names = [r["COLUMN_NAME"] for r in columns if "GENERATED" not in r["EXTRA"]]
                columns_sql = ",".join("`" + n.replace("`", "``") + "`" for n in names)
                # PyMySQL's negative timedelta encoder is not a lossless TIME
                # transport. Preserve the server's exact textual TIME instead.
                select_sql = ",".join((f"CAST({quote_identifier(r['COLUMN_NAME'])} AS CHAR)" if r["DATA_TYPE"] == "time"
                                       else quote_identifier(r["COLUMN_NAME"]))
                                      for r in columns if "GENERATED" not in r["EXTRA"])
                with source.connection.cursor() as cursor:
                    cursor.execute(f"SELECT {select_sql} FROM {qualified(SCHEMA, name)} ORDER BY id")
                    rows = cursor.fetchall()
                with db.connection.cursor() as cursor:
                    cursor.executemany(f"INSERT INTO {qualified(SCHEMA, name)} ({columns_sql}) VALUES ({','.join(['%s'] * len(names))})", rows)
                assert int(db.scalar(f"SELECT COUNT(*) FROM {qualified(SCHEMA, name)}")) == len(rows)
                if name != "pseudo_fixture":
                    db.query(f"ANALYZE TABLE {qualified(SCHEMA, name)}")
        print(json.dumps({"target": target, "import_exit": outcome["exit_code"], "verification_issues": issues, "fixture_copy": "completed"}), flush=True)


if __name__ == "__main__":
    main()
