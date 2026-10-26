from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext

from pcc_tool.capture.guard import SYSTEM_SCHEMAS, dependencies, guard_sql
from pcc_tool.models import METRICS, PCCError, QueryCase, digest, primitive

IDENTITY = ("source_cluster", "source_epoch", "instance", "summary_begin_time", "schema_name",
            "sql_digest", "plan_digest", "previous_statement_key")


def instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise PCCError("INVALID_TIME", "时间必须为带时区的 ISO 8601 格式") from None


def number(value, optional=False) -> Decimal | None:
    if value is None and optional:
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise PCCError("INVALID_COUNTER", "计数器必须为有限非负十进制数") from None


def merge_snapshots(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    groups, issues = defaultdict(list), []
    for raw in rows:
        r = dict(raw)
        if "previous_statement_key" not in r:
            r["previous_statement_key"] = r.get("prev_sample_digest") or digest(r.get("prev_sample_text", ""))
            r["key_quality"] = "lossy"
        # TiDB returns SQL NULL for statements executed without a default database.
        # Normalize optional identity strings before filtering and tuple sorting.
        for field in IDENTITY:
            if r.get(field) is None:
                r[field] = "unknown" if field == "source_epoch" else ""
        if not r.get("sql_digest") or r["schema_name"].lower() in SYSTEM_SCHEMAS:
            continue
        names = r.get("table_names", "") or ""
        if any(t.strip().split(".")[0].lower() in SYSTEM_SCHEMAS for t in names.split(",") if "." in t):
            continue
        r.setdefault("source_epoch", "unknown")
        if r.get("counter_reappeared"):
            issues.append({"code": "COUNTER_RESET_OR_AMBIGUOUS", "message": "累计计数器缺采后重新出现，可能发生驱逐或重启",
                           "identity": {k: r.get(k) for k in IDENTITY}})
        groups[tuple(r.get(k, "") for k in IDENTITY)].append(r)
    merged = []
    for key, versions in sorted(groups.items()):
        versions.sort(key=lambda r: (instant(r["collected_at"]), digest(r)))
        chosen, samples = None, {}
        for row in versions:
            count = number(row.get("exec_count"))
            latency = number(row.get("sum_latency"))
            if count != count.to_integral_value() or latency != latency.to_integral_value():
                raise PCCError("INVALID_COUNTER", "次数和 ns 延迟必须为整数")
            origin = {k: row.get(k) for k in IDENTITY}
            origin.update(collected_at=row["collected_at"], summary_end_time=row["summary_end_time"],
                          source_plan=row.get("plan"), historical_session=row.get("context", "unknown"),
                          key_quality=row.get("key_quality", "native"))
            sample = {"sql": row.get("query_sample_text") or None, "origin": origin,
                      "context": row.get("context", {}), "truncated": row.get("truncated", False)}
            samples[digest(sample)] = sample
            if chosen is None:
                chosen = row
                continue
            before = (number(chosen["exec_count"]), number(chosen["sum_latency"]))
            after = (count, latency)
            if all(a >= b for a, b in zip(after, before)):
                chosen = row
            elif after != before:
                issues.append({"code": "COUNTER_RESET_OR_AMBIGUOUS", "identity": dict(zip(IDENTITY, key)),
                               "message": "累计向量回退或不一致；保留已知累计值，不相加", "observed": primitive(after)})
        merged.append({**chosen, "samples": list(samples.values())})
    return merged, issues


def select_queries(rows: list[dict], *, begin: str, end: str, top_n=20, order_by=METRICS,
                   ranking_scope="global", schemas=(), statement_types=("select", "update", "delete")):
    start, stop = instant(begin), instant(end)
    if start >= stop or top_n <= 0 or ranking_scope not in {"global", "per_schema"}:
        raise PCCError("INVALID_SELECTION", "时间范围、Top N 或排名范围无效")
    if not order_by or any(m not in METRICS for m in order_by):
        raise PCCError("INVALID_SELECTION", "排名指标无效")
    # Merge the whole history first, then apply the original-window overlap predicate.
    merged, issues = merge_snapshots(rows)
    groups = defaultdict(list)
    for r in merged:
        if not instant(r["summary_begin_time"]) < stop or not instant(r["summary_end_time"]) > start:
            continue
        schema = r.get("schema_name", "")
        kind = (r.get("stmt_type") or "").lower()
        if kind == "with":
            kind = "select"
        if (schemas and schema not in schemas) or kind not in statement_types:
            continue
        groups[(r["source_cluster"], schema, r["sql_digest"])].append(r)
    candidates = []
    for key, values in sorted(groups.items()):
        with localcontext() as decimal_context:
            decimal_context.prec = 100
            count = sum(number(r["exec_count"]) for r in values)
            latency = sum(number(r["sum_latency"]) for r in values)
            keys = None if any(r.get("avg_total_keys") is None for r in values) else sum(
                number(r["avg_total_keys"]) * number(r["exec_count"]) for r in values)
            metrics = dict(execution_count=count, total_latency=latency,
                           average_latency=latency / count if count else None,
                           total_keys=keys, average_keys=keys / count if keys is not None and count else None)
        candidates.append(dict(key=key, metrics=metrics, rows=values, ranks={}, keys_approximate=keys is not None))
    for metric in order_by:
        if any(c["metrics"][metric] is None for c in candidates):
            raise PCCError("METRIC_UNAVAILABLE", f"{metric} 缺失或分母为零，请显式选择可用指标")
    scopes = sorted({c["key"][1] for c in candidates}) if ranking_scope == "per_schema" else [None]
    for scope in scopes:
        pool = [c for c in candidates if scope is None or c["key"][1] == scope]
        for metric in order_by:
            ranked = sorted(pool, key=lambda c: (c["metrics"][metric].copy_negate(), c["key"][1], c["key"][2], c["key"][0]))
            for rank, candidate in enumerate(ranked[:top_n], 1):
                candidate["ranks"][metric] = rank
    selected = [c for c in candidates if c["ranks"]]
    queries = {}
    for candidate in selected:
        cluster, schema, source_digest = candidate["key"]
        for row in candidate["rows"]:
            for sample in row["samples"]:
                q = make_query(sample["sql"], schema, sample["origin"], sample["context"],
                               statement_types, bool(sample["truncated"]))
                for table_ref in (row.get("table_names") or "").split(","):
                    table_ref = table_ref.strip()
                    if not table_ref:
                        continue
                    if table_ref.count(".") == 1:
                        pair = table_ref.split(".", 1)
                        if pair not in q.dependencies:
                            q.dependencies.append(pair)
                    else:
                        q.issues.append({"code": "DEPENDENCY_UNRESOLVED", "query_id": q.query_id,
                                         "message": "TABLE_NAMES 包含无法无歧义拆分的对象"})
                q.source_cluster, q.source_digest = cluster, source_digest
                q.metrics = primitive({**candidate["metrics"], "ranks": candidate["ranks"],
                                       "keys_approximate": candidate["keys_approximate"]})
                if row.get("plan"):
                    q.source_plan_refs = [{"plan": row["plan"], "kind": "historical_evidence_only"}]
                if q.query_id in queries:
                    existing = queries[q.query_id]
                    existing.sample_origins.extend(q.sample_origins)
                    existing.source_plan_refs.extend(q.source_plan_refs)
                else:
                    queries[q.query_id] = q
    if not queries:
        raise PCCError("NO_QUERIES_SELECTED", "没有符合条件的 SQL")
    selection = {"begin": begin, "end": end, "window_semantics": "overlapping_cumulative_windows",
                 "top_n": top_n, "ranking_scope": ranking_scope, "schemas": schemas,
                 "statement_types": statement_types, "order_by": order_by,
                 "selected_digests": len(selected), "visible_digests": len(candidates),
                 "coverage": "visible_summary_only", "rankings": [
                     {k: v for k, v in c.items() if k != "rows"} for c in selected]}
    return list(queries.values()), primitive(selection), issues


def make_query(sql, schema="", origin=None, context=None, allowed=("select", "update", "delete"), truncated=False, parameters=None):
    q = QueryCase.create(sql, schema, context, origin, parameters=parameters)
    try:
        if truncated or sql and any(0xDC80 <= ord(c) <= 0xDCFF for c in sql):
            raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "来源标记 SQL 已截断")
        tree, q.statement_type = guard_sql(sql, allowed, parameters=parameters)
        q.dependencies = dependencies(tree, schema)
    except PCCError as exc:
        q.executable_status = "needs_sample" if exc.code == "NEEDS_EXECUTABLE_SAMPLE" else "unsupported"
        q.issues.append({"code": exc.code, "message": exc.message, "query_id": q.query_id})
    return q
