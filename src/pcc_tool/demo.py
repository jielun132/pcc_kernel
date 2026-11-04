"""Portable, offline copy of the append-only SQL corpus and synthetic fixtures."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from pcc_tool.artifacts.store import Publication, write_bytes, write_json, write_jsonl
from pcc_tool.models import PCCError, digest

SCHEMAS = ("business", "reporting", "archive")


def catalog():
    resource = files("pcc_tool") / "demo_data"
    lines = (resource / "sql_cases.jsonl").read_bytes().splitlines(keepends=True)
    locked = json.loads((resource / "catalog.lock.json").read_text("utf-8"))
    cases = [json.loads(line) for line in lines]
    if len(lines) != len(locked) or any(
        case["id"] != lock["id"] or hashlib.sha256(line).hexdigest() != lock["sha256"]
        for line, case, lock in zip(lines, cases, locked)
    ):
        raise PCCError("DEMO_CATALOG_INVALID", "内置案例校验失败")
    return cases


def literal(value):
    # Hex literals also work with NO_BACKSLASH_ESCAPES and arbitrary Unicode.
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, (int, float)):
        return str(value)
    return "CONVERT(X'" + str(value).encode("utf-8").hex() + "' USING utf8mb4)"


def fixture_sql(schemas):
    resource = files("pcc_tool") / "demo_data"
    templates = [(resource / name).read_text("utf-8")
                 for name in ("live_setup.sql", "sql_matrix_setup.sql")]
    output = ["-- Synthetic data only. Run ONCE on a dedicated source test cluster.",
              "-- Generate a new demo for another run. No DROP, TRUNCATE or IF NOT EXISTS.",
              "SET NAMES utf8mb4;"]
    for schema in schemas:
        output.extend(text.replace("pcc_validation_20261011", schema) for text in templates)
        data = {
            "customers": [(i, i % 5, f"customer-{i}") for i in range(1, 101)],
            "orders": [(i, i % 100 + 1, i * 37 % 2000, i % 4, f"item-{i} 100%") for i in range(1, 1001)],
            "items": [(i, i % 1000 + 1, i % 10, i * 13 % 500) for i in range(1, 2001)],
            "partitioned_orders": [(i, i * 11 % 400) for i in range(240)],
        }
        for table, rows in data.items():
            for start in range(0, len(rows), 200):
                values = ",\n".join("(" + ",".join(map(literal, row)) + ")" for row in rows[start:start + 200])
                output.append(f"INSERT INTO `{schema}`.`{table}` VALUES\n{values};")
            output.append(f"ANALYZE TABLE `{schema}`.`{table}`;")
        columns = ("id,tiny,small,medium,signed_int,signed_big,unsigned_big,f,d,money,precise_value,bits,flag,"
                   "fixed_char,text_value,ci_value,binary_value,varbinary_value,tiny_text,text_body,medium_text,"
                   "long_text,tiny_blob,blob_value,medium_blob,long_blob,day_value,dt,ts,duration,yr,enum_value,"
                   "set_value,doc,nullable_value")
        for i in range(32):
            values = [i, i-16, i*100, i*1000, i, i*1000000, 2**63+i, 1.25, 1.125,
                      f"{i}.123456", "123456789012345678901234567890.123456789012345678901234567890",
                      i, i%2, "fixed", f"中文😀'?,;\\%s-{i}", f"Case-{i}", b"a\x00b", b"\xff\x00",
                      "tiny", "body", "medium", "long", b"tiny", b"\xff\x00", b"medium", b"long",
                      "2024-02-29", "2024-02-29 12:34:56.123456", "2024-02-29 12:34:56.123456",
                      "-12:34:56.123456", 2024, "paid", "a,c", json.dumps({"n": i, "s": "?,;中文"}),
                      None if i%2 else i]
            output.append(f"INSERT INTO `{schema}`.matrix_types ({columns}) VALUES ({','.join(map(literal, values))});")
        output.append(f"ANALYZE TABLE `{schema}`.matrix_types;")
    output.append("SELECT 'PCC demo setup completed' AS result;")
    return "\n".join(output) + "\n"


def generate(out, schema_prefix=None):
    prefix = schema_prefix or "pcc_demo_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3)
    if not re.fullmatch(r"pcc_[a-z0-9_]{1,40}", prefix):
        raise PCCError("INVALID_CONFIG", "演示库前缀须为 pcc_ 加 1–40 位小写字母、数字或下划线")
    schemas = {"pcc_case_" + name: prefix + "_" + name for name in SCHEMAS}
    cases = catalog()
    samples, index = [], []
    for case in cases:
        sql = case["sql_text"]
        for logical, actual in schemas.items():
            sql = sql.replace(logical, actual)
        samples.append({"sql_text": sql, "schema": schemas[case["schema"]], "parameters": case["parameters"],
                        "origin": {"case_id": case["id"], "case_name": case["name"], "category": case["category"]},
                        "source_cluster": "pcc-synthetic-demo"})
        index.append({**case, "schema": schemas[case["schema"]], "sql_text": sql})
    metadata = {"schema_prefix": prefix, "schemas": list(schemas.values()), "case_count": len(cases),
                "categories": dict(Counter(c["category"] for c in cases)),
                "expected_guards": dict(Counter(c["expected_guard"] for c in cases))}
    with Publication(str(out), digest(metadata)) as publication:
        write_bytes(publication.work / "setup.sql", fixture_sql(schemas.values()).encode("utf-8"))
        write_jsonl(publication.work / "queries.jsonl", samples)
        write_json(publication.work / "case-index.json", index)
        write_json(publication.work / "demo.json", metadata)
        rows = ["# SQL 案例索引", "", "使用 queries.jsonl 全量比较；setup.sql 只建表造数。", "",
                "| 编号 | 类别 | 名称 | 预期守卫 |", "|---|---|---|---|"]
        rows.extend(f"| {c['id']} | {c['category']} | {c['name']} | {c['expected_guard']} |" for c in cases)
        write_bytes(publication.work / "CASES.md", ("\n".join(rows) + "\n").encode("utf-8"))
        publication.checkpoint()
        publication.publish({"kind": "DemoCases", "run_status": "completed", **metadata})
    return {"output_dir": str(Path(out).resolve()), "run_status": "completed", **metadata}
