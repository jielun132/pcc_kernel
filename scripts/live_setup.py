"""Explicitly opt-in, small fixtures for the approved single-cluster validation."""
import argparse
from pathlib import Path

import sqlglot
from sqlglot.tokens import TokenType

from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.models import canonical

SCHEMA = "pcc_validation_20261011"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--approve-schema", required=True, choices=[SCHEMA])
    args = parser.parse_args()
    profiles = load_profiles(args.config)
    with Database(profiles["source"]) as db:
        version = db.version()
        if db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (SCHEMA,)) != "0":
            raise RuntimeError("Fixture schema already exists; refuse to overwrite or reuse it")
        script = Path(__file__).resolve().parents[1] / "tests/fixtures/live_setup.sql"
        # Execute the reviewed DDL verbatim; SQLGlot does not parse TiDB partition DDL.
        text, start = script.read_text(), 0
        for token in sqlglot.tokenize(text, read="mysql"):
            if token.token_type == TokenType.SEMICOLON:
                db.query(text[start:token.end + 1])
                start = token.end + 1
        data = {
            "customers": [(i, i % 5, f"customer-{i}") for i in range(1, 101)],
            "orders": [(i, i % 100 + 1, i * 37 % 2000, i % 4, f"item-{i} 100%") for i in range(1, 1001)],
            "items": [(i, i % 1000 + 1, i % 10, i * 13 % 500) for i in range(1, 2001)],
            "partitioned_orders": [(i, i * 11 % 400) for i in range(240)],
            "pseudo_fixture": [(i, i % 3) for i in range(8)],
            "drift_fixture": [(i, i % 4) for i in range(16)],
        }
        for table, rows in data.items():
            placeholders = ",".join(["%s"] * len(rows[0]))
            with db.connection.cursor() as cursor:
                cursor.executemany(f"INSERT INTO `{SCHEMA}`.`{table}` VALUES ({placeholders})", rows)
        for table in data:
            if table != "pseudo_fixture":
                db.query(f"ANALYZE TABLE `{SCHEMA}`.`{table}`")
        counts = {t: db.scalar(f"SELECT COUNT(*) FROM `{SCHEMA}`.`{t}`") for t in data}
        assert counts == {t: str(len(rows)) for t, rows in data.items()}
        print(canonical({"version": version, "schema": SCHEMA, "counts": counts, "total_rows": sum(len(r) for r in data.values())}))


if __name__ == "__main__":
    main()
