from decimal import Decimal

import pytest

from pcc_tool.collector.summary import merge_snapshots, select_queries
from pcc_tool.models import METRICS, PCCError, QueryCase

WINDOW = {"begin": "2026-10-08T09:00:00+08:00", "end": "2026-10-08T10:00:00+08:00"}


def test_monotonic_late_duplicate_and_cross_instance(summary_row):
    rows = [summary_row(exec_count=str(n), sum_latency=str(n * 10),
                        collected_at=f"2026-10-08T09:{minute:02}:00+08:00")
            for n, minute in [(10, 10), (15, 15), (20, 20), (11, 11), (15, 15)]]
    rows.append(summary_row(instance="b:4000", exec_count="7", sum_latency="70"))
    merged, issues = merge_snapshots(list(reversed(rows)))
    assert sorted(int(r["exec_count"]) for r in merged) == [7, 20]
    assert not issues
    queries, selection, _ = select_queries(rows, **WINDOW)
    assert len(queries) == 1
    assert Decimal(queries[0].metrics["execution_count"]) == 27
    assert Decimal(queries[0].metrics["average_latency"]) == 10
    assert Decimal(queries[0].metrics["total_keys"]) == Decimal("67.5")
    assert queries[0].metrics["keys_approximate"]


def test_key_ignores_window_end_and_pairs_average(summary_row):
    rows = [summary_row(exec_count="10", avg_total_keys="100"),
            summary_row(exec_count="20", sum_latency="200", avg_total_keys="2", summary_end_time="2026-10-08T09:40:00+08:00",
                        collected_at="2026-10-08T09:20:00+08:00")]
    q, _, _ = select_queries(rows, **WINDOW)
    assert Decimal(q[0].metrics["total_keys"]) == 40


def test_inconsistent_vector_reset_reported(summary_row):
    rows = [summary_row(exec_count="20", sum_latency="200"),
            summary_row(exec_count="30", sum_latency="100", collected_at="2026-10-08T09:20:00+08:00")]
    merged, issues = merge_snapshots(rows)
    assert merged[0]["exec_count"] == "20"
    assert {i["code"] for i in issues} == {"COUNTER_RESET_OR_AMBIGUOUS"}


def test_epochs_are_separate(summary_row):
    merged, _ = merge_snapshots([summary_row(source_epoch="one"), summary_row(source_epoch="two")])
    assert len(merged) == 2


def test_weighted_average_and_parameter_samples(summary_row):
    q, _, _ = select_queries([summary_row(exec_count="1", sum_latency="1000"),
        summary_row(instance="b", exec_count="99", sum_latency="990", query_sample_text="SELECT * FROM t WHERE id=2")], **WINDOW)
    assert len(q) == 2
    assert len({i.query_id for i in q}) == 2
    assert all(Decimal(i.metrics["average_latency"]) == Decimal("19.9") for i in q)


def test_top_union_stable_and_scope(summary_row):
    rows = [summary_row(sql_digest=f"d{i:02}", schema_name=s, exec_count=str(i+1), sum_latency=str(100-i),
                        avg_total_keys=str(2*i), query_sample_text=f"SELECT * FROM t WHERE id={i}")
            for s in ("a", "b") for i in range(30)]
    queries, selection, _ = select_queries(rows, top_n=20, **WINDOW)
    assert int(selection["selected_digests"]) <= 100
    assert selection == select_queries(list(reversed(rows)), top_n=20, **WINDOW)[1]
    per_schema = select_queries(rows, top_n=20, ranking_scope="per_schema", **WINDOW)[1]
    assert int(per_schema["selected_digests"]) >= int(selection["selected_digests"])
    filtered = select_queries(rows, top_n=1, schemas=("b",), **WINDOW)[0]
    assert all(q.schema == "b" for q in filtered)


def test_missing_metrics_never_zero(summary_row):
    rows = [summary_row(avg_total_keys=None)]
    with pytest.raises(PCCError, match="total_keys"):
        select_queries(rows, **WINDOW)
    assert select_queries(rows, order_by=METRICS[:3], **WINDOW)[0][0].metrics["total_keys"] is None
    with pytest.raises(PCCError) as err:
        select_queries([summary_row(exec_count="0", sum_latency="0")], **WINDOW)
    assert err.value.code == "METRIC_UNAVAILABLE"


def test_interval_overlap_not_prorated(summary_row):
    q, _, _ = select_queries([summary_row()], begin="2026-10-08T09:29:00+08:00", end="2026-10-08T09:30:00+08:00")
    assert Decimal(q[0].metrics["execution_count"]) == 10
    with pytest.raises(PCCError) as err:
        select_queries([summary_row()], begin="2026-10-08T09:30:00+08:00", end="2026-10-08T10:00:00+08:00")
    assert err.value.code == "NO_QUERIES_SELECTED"


def test_missing_samples_have_unique_ids(summary_row):
    q, _, _ = select_queries([summary_row(query_sample_text=None), summary_row(query_sample_text=None, sql_digest="other")], **WINDOW)
    assert len(q) == 2 and len({x.query_id for x in q}) == 2
    assert all(x.sql_text is None and x.executable_status == "needs_sample" for x in q)


def test_system_rows_and_others_excluded(summary_row):
    merged, _ = merge_snapshots([summary_row(schema_name="mysql"), summary_row(sql_digest=None), summary_row(table_names="mysql.user")])
    assert not merged


def test_exact_large_numbers(summary_row):
    value = "999999999999999999999999"
    q, _, _ = select_queries([summary_row(exec_count=value, sum_latency=value)], **WINDOW)
    assert q[0].metrics["execution_count"] == value
    assert q[0].metrics["average_latency"] == "1"


def test_missing_sample_repeated_snapshots_one_placeholder(summary_row):
    rows = [summary_row(query_sample_text=None), summary_row(query_sample_text=None, exec_count="20", sum_latency="200",
             collected_at="2026-10-08T09:20:00+08:00")]
    q, _, _ = select_queries(rows, **WINDOW)
    assert len(q) == 1 and len(q[0].sample_origins) == 2


def test_30_digit_counter_order_not_rounded(summary_row):
    small, large = "123456789012345678901234567890", "123456789012345678901234567891"
    rows = [summary_row(sql_digest="a", exec_count=small, sum_latency=small),
            summary_row(sql_digest="z", exec_count=large, sum_latency=large)]
    q, selection, _ = select_queries(rows, order_by=("execution_count",), top_n=1, **WINDOW)
    assert q[0].source_digest == "z" and q[0].metrics["execution_count"] == large
