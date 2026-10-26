from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pcc_tool.capture.connection import Database
from pcc_tool.capture.guard import quote_identifier
from pcc_tool.models import PCCError, digest, now
from .summary import instant

SUMMARY_COLUMNS = ("SUMMARY_BEGIN_TIME", "SUMMARY_END_TIME", "STMT_TYPE", "SCHEMA_NAME", "DIGEST", "DIGEST_TEXT",
                   "TABLE_NAMES", "EXEC_COUNT", "SUM_LATENCY", "AVG_TOTAL_KEYS", "QUERY_SAMPLE_TEXT",
                   "PREV_SAMPLE_TEXT", "PLAN_DIGEST", "PLAN", "INSTANCE")
SUMMARY_VARIABLES = ("tidb_enable_stmt_summary", "tidb_stmt_summary_refresh_interval", "tidb_stmt_summary_history_size",
                     "tidb_stmt_summary_max_stmt_count", "tidb_stmt_summary_max_sql_length", "tidb_enable_persistent_stats",
                     "tidb_stmt_summary_enable_persistent")


def read_summary(db: Database, source: str, begin=None, end=None):
    profile = db.profile
    source_cluster = profile.get("source_cluster", source)
    zone_name = profile.get("timezone")
    if not zone_name:
        raise PCCError("SOURCE_TIMEZONE_REQUIRED", "Summary 采集需明确配置源端 timezone")
    zone = ZoneInfo(zone_name)
    variables = db.variables(SUMMARY_VARIABLES)
    if str(variables.get("tidb_enable_stmt_summary")).lower() in {"0", "off"}:
        raise PCCError("SUMMARY_DISABLED", "源端 Statement Summary 未开启")
    if begin is None:
        seconds = variables.get("tidb_stmt_summary_refresh_interval")
        if not seconds:
            raise PCCError("SUMMARY_CAPABILITY_MISSING", "无法确定实际 Summary 窗口")
        begin = (datetime.now(timezone.utc) - timedelta(seconds=int(seconds))).isoformat()
        end = now()
    start, stop = instant(begin), instant(end)
    epochs, warnings = {}, []
    try:
        info = db.cluster_info()
        epochs = {r["INSTANCE"]: r["START_TIME"] for r in info if str(r["TYPE"]).lower() == "tidb"}
    except PCCError:
        warnings.append({"code": "SOURCE_EPOCH_UNKNOWN", "message": "实例启动代际不可见"})
    rows, capabilities, collected_at = [], {}, now()
    for table in ("CLUSTER_STATEMENTS_SUMMARY", "CLUSTER_STATEMENTS_SUMMARY_HISTORY"):
        columns = db.query(f"SELECT * FROM information_schema.{table} LIMIT 0")[0]
        available = {c.upper() for c in columns}
        required = {"SUMMARY_BEGIN_TIME", "SUMMARY_END_TIME", "EXEC_COUNT", "SUM_LATENCY", "DIGEST", "INSTANCE"}
        if not required <= available:
            raise PCCError("SUMMARY_CAPABILITY_MISSING", "Statement Summary 缺少必要列")
        chosen = [c for c in SUMMARY_COLUMNS if c in available]
        capabilities[table] = chosen
        query = ("SELECT " + ",".join(quote_identifier(c) for c in chosen) + f" FROM information_schema.{table} "
                 "WHERE SUMMARY_BEGIN_TIME < %s AND SUMMARY_END_TIME > %s AND DIGEST IS NOT NULL")
        values = db.records(query, (stop.astimezone(zone).replace(tzinfo=None), start.astimezone(zone).replace(tzinfo=None)))
        source_warnings = db.records("SHOW WARNINGS")
        if source_warnings:
            warnings.append({"code": "SUMMARY_READ_WARNINGS", "table": table, "warnings": source_warnings})
        for raw in values:
            r = {k.lower(): v for k, v in raw.items()}
            r["sql_digest"] = r.pop("digest")
            for key in ("summary_begin_time", "summary_end_time"):
                d = datetime.fromisoformat(r[key])
                r[key] = d.replace(tzinfo=zone).isoformat() if d.tzinfo is None else d.isoformat()
            maximum = variables.get("tidb_stmt_summary_max_sql_length")
            sample = r.get("query_sample_text") or ""
            r.update(source_cluster=source_cluster, source_epoch=epochs.get(r["instance"], "unknown"),
                     collected_at=collected_at, key_quality="lossy", context={},
                     previous_statement_key=digest(r.get("prev_sample_text", "")),
                     closed_observed=instant(r["summary_end_time"]) <= instant(collected_at),
                     truncated=bool(any(0xDC80 <= ord(c) <= 0xDCFF for c in sample)
                                    or maximum and len(sample.encode("utf-8", "surrogateescape")) >= int(maximum)),
                     source_table=table)
            rows.append(r)
    present = {r["instance"] for r in rows}
    for missing in sorted(set(epochs) - present):
        warnings.append({"code": "INSTANCE_NO_VISIBLE_ROWS", "instance": missing,
                         "message": "无法区分无流量与缺采，不能视为完整覆盖"})
    try:
        evicted = db.records("SELECT * FROM information_schema.CLUSTER_STATEMENTS_SUMMARY_EVICTED")
    except PCCError:
        evicted = None
        warnings.append({"code": "EVICTION_VISIBILITY_UNKNOWN"})
    return rows, {"version": db.version(), "collected_at": collected_at, "variables": variables,
                  "capabilities": capabilities, "epochs": epochs, "issues": warnings, "eviction": evicted,
                  "historical_session": "unknown", "visibility": "grants_and_retention_limited"}


def read_repository(db: Database, source: str, begin: str, end: str):
    from .legacy import COLUMNS
    zone_name = db.profile.get("timezone")
    if not zone_name:
        raise PCCError("SOURCE_TIMEZONE_REQUIRED", "旧 repository 需要明确时区")
    zone = ZoneInfo(zone_name)
    columns = ("source_cluster", "last_collect_time", "is_final", "instance", "summary_begin_time", "summary_end_time",
               "stmt_type", "sql_text", "query_sample_text", "prev_sample_text", "prev_sample_digest", "sql_digest",
               "schema_name", "table_names", "plan_digest", "exec_count", "sum_latency_ns", "max_latency_ns", "min_latency_ns")
    sql = "SELECT " + ",".join(quote_identifier(c) for c in columns) + " FROM pcc_repository.stmt_summary_window WHERE summary_begin_time < %s AND summary_end_time > %s AND source_cluster = %s"
    _, values = db.query(sql, (instant(end).astimezone(zone).replace(tzinfo=None),
                               instant(begin).astimezone(zone).replace(tzinfo=None), source))
    rows = []
    for value in values:
        row = dict(zip(COLUMNS, value))
        for key in ("collected_at", "summary_begin_time", "summary_end_time"):
            row[key] = datetime.fromisoformat(row[key]).replace(tzinfo=zone).isoformat()
        row.update(source_epoch="unknown", key_quality="lossy", avg_total_keys=None)
        rows.append(row)
    return rows
