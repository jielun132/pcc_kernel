import pytest

from sql_matrix import ALLOWED, CASES
from pcc_tool.collector.summary import make_query


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_matrix_guard_and_dependencies(case):
    q = make_query(case["sql_text"], case["schema"], allowed=ALLOWED, parameters=case["parameters"])
    assert q.executable_status == "ok", q.issues
    assert q.sql_text == case["sql_text"]
    assert q.parameters == case["parameters"]
    assert q.dependencies and all(p[0] == case["schema"] for p in q.dependencies)
