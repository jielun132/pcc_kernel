"""Read-only evidence of values delivered through SQL and binary parameters."""
import argparse
import os
from datetime import timedelta

import mysql.connector

from pcc_tool.artifacts.store import write_json
from pcc_tool.capture.connection import Database, clean_cell, load_profiles
from pcc_tool.capture.parameters import parameter_bindings
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = {}
    cases = [("negative_time", "time", "-12:34:56.123456", -timedelta(hours=12,minutes=34,seconds=56,microseconds=123456)),
             ("positive_time", "time", "12:34:56.123456", timedelta(hours=12,minutes=34,seconds=56,microseconds=123456)),
             ("json_typed_vs_text", "json", '{"n":3}', '{"n":3}'),
             ("negative_time_text", "string", "-12:34:56.123456", "-12:34:56.123456"),
             ("json_text", "string", '{"n":3}', '{"n":3}')]
    for label, profile in load_profiles(args.config).items():
        rows = []
        with Database(profile) as db:
            version = db.scalar("SELECT TIDB_VERSION()")
            connection = mysql.connector.connect(host=profile["host"],port=int(profile["port"]),user=profile["user"],
                password=os.environ.get(profile.get("password_env", ""), ""),use_pure=True,use_unicode=False,
                charset="utf8mb4",connection_timeout=5,autocommit=True)
            try:
                for name, kind, value, binary_value in cases:
                    expression, bound = parameter_bindings([{"type":kind,"value":value}])[0]
                    db.query("SET @pcc_value=" + expression, (bound,))
                    sql_value = db.scalar("SELECT CAST(@pcc_value AS CHAR)")
                    with connection.cursor(prepared=True) as cursor:
                        cursor.execute("SELECT CAST(? AS CHAR)", (binary_value,))
                        returned = cursor.fetchone()[0]
                        actual = clean_cell(bytes(returned) if isinstance(returned, bytearray) else returned)
                    rows.append({"name":name,"declared_type":kind,"input":value,"sql_value":sql_value,"binary_value":actual})
            finally:
                connection.close()
        result[label] = {"version":version,"connector":mysql.connector.__version__,"cases":rows}
    write_json(Path(args.out),result)


if __name__ == "__main__":
    main()
