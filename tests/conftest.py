import pytest

from pcc_tool.models import PlanSnapshot

COLS = ["id", "estRows", "task", "access object", "operator info"]


@pytest.fixture
def snapshot():
    def make(rows, side="old", query_id="q", **kwargs):
        return PlanSnapshot(query_id, side, columns=COLS, rows=rows, **kwargs)
    return make


@pytest.fixture
def summary_row():
    def make(**kwargs):
        return {"source_cluster": "test", "source_epoch": "epoch1", "instance": "a:4000",
                "summary_begin_time": "2026-10-08T09:00:00+08:00", "summary_end_time": "2026-10-08T09:30:00+08:00",
                "collected_at": "2026-10-08T09:10:00+08:00", "schema_name": "app", "sql_digest": "digest1",
                "plan_digest": "p1", "stmt_type": "select", "previous_statement_key": "", "exec_count": "10",
                "sum_latency": "100", "avg_total_keys": "2.5", "query_sample_text": "SELECT * FROM t WHERE id=1", **kwargs}
    return make
