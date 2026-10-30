"""Load the append-only corpus without generating or rewriting historical cases."""
import json
from pathlib import Path

CATALOG = Path(__file__).parent / "cases" / "sql_cases.jsonl"
SCHEMAS = ("pcc_case_business", "pcc_case_reporting", "pcc_case_archive")


def load_cases():
    return [json.loads(line) for line in CATALOG.read_text().splitlines() if line]


def materialize(case, schemas):
    sql = case["sql_text"]
    for logical, actual in schemas.items():
        sql = sql.replace(logical, actual)
    return dict(sql_text=sql, schema=schemas[case["schema"]], parameters=case["parameters"],
                origin=dict(case_id=case["id"], case_name=case["name"], category=case["category"],
                            source_cluster="本地 Docker 用例验收（TiDB 6.5.12 / 8.5.5）"))
