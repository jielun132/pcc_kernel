import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from case_catalog import CATALOG, SCHEMAS, load_cases, materialize
from pcc_tool.collector.summary import make_query

spec = importlib.util.spec_from_file_location("catalog_check", Path(__file__).parents[2] / "scripts/check_case_catalog.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"] + "-" + c["name"])
def test_catalog_sql_guard_and_original_text(case):
    mapping = {name: name + "_isolated" for name in SCHEMAS}
    row = materialize(case, mapping)
    q = make_query(row["sql_text"], row["schema"], allowed=case["allowed"], parameters=row["parameters"])
    assert q.executable_status == case["expected_guard"], q.issues
    assert q.sql_text == row["sql_text"] and q.parameters == case["parameters"]
    if case["expected_issue"]:
        assert case["expected_issue"] in [i["code"] for i in q.issues]
    else:
        assert q.dependencies and all(pair[0] in mapping.values() for pair in q.dependencies)


def test_catalog_content_matches_locked_history():
    lock = json.loads(CATALOG.with_name("catalog.lock.json").read_text())
    assert check.validate(CATALOG.read_bytes(), lock) == len(CASES)
    assert len(CASES) >= 413


def test_history_only_accepts_appends():
    lines = [json.dumps(dict(id=f"SQL-{i}", sql_text=f"SELECT {i}")).encode() + b"\n" for i in range(3)]
    lock = [dict(id=f"SQL-{i}", sha256=hashlib.sha256(line).hexdigest()) for i, line in enumerate(lines)]
    assert check.validate(b"".join(lines), lock, b"".join(lines[:2]), lock[:2]) == 3
    for changed in (lines[1:], list(reversed(lines)), [lines[0].replace(b"SELECT", b"select"), *lines[1:]]):
        with pytest.raises(ValueError):
            check.validate(b"".join(changed), lock, b"".join(lines[:2]), lock[:2])
    with pytest.raises(ValueError):
        check.validate(b"".join(lines), lock, b"".join(lines[:2]), list(reversed(lock[:2])))
