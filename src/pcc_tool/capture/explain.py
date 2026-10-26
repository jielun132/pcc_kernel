from __future__ import annotations

import threading
import time

from pcc_tool.capture.connection import Database
from pcc_tool.capture.guard import guard_sql, quote_identifier, validate_session
from pcc_tool.capture.parameters import prepare_explain
from pcc_tool.models import PCCError, PlanSnapshot, QueryCase, now


def negotiate_format(db: Database) -> list[str]:
    supported = []
    for fmt in ("tidb_json", "row"):
        try:
            columns, rows = db.query(f"EXPLAIN FORMAT='{fmt}' SELECT 1")
            db.query("SHOW WARNINGS")
            from pcc_tool.plan.parser import parse_plan
            if parse_plan(PlanSnapshot("probe", "probe", format=fmt, columns=columns, rows=rows)).parse_status != "failed":
                supported.append(fmt)
        except PCCError:
            continue
    if not supported:
        raise PCCError("PLAN_FORMAT_UNSUPPORTED", "无法读取已验证结构的 EXPLAIN 格式")
    return supported


def capture_plan(query: QueryCase, side: str, profile: dict, *, format="row", session=None,
                 timeout_seconds=30, stats_retries=1, binding_policy="exclude", cancel=None,
                 db_factory=Database) -> PlanSnapshot:
    snapshot = PlanSnapshot(query.query_id, side, format=format)
    cancel = cancel or threading.Event()
    if cancel.is_set():
        snapshot.capture_status = "skipped"
        return snapshot
    if query.executable_status != "ok":
        snapshot.capture_status = "needs_sample" if query.executable_status == "needs_sample" else "skipped"
        snapshot.issues.extend(query.issues)
        return snapshot
    try:
        if format not in {"row", "tidb_json"}:
            raise PCCError("PLAN_FORMAT_UNSUPPORTED", "不支持的 EXPLAIN 格式")
        guard_sql(query.sql_text, ("select", "update", "delete", query.statement_type), parameters=query.parameters)
        settings = validate_session({**profile.get("session", {}), **query.context, **(session or {})})
        with db_factory(profile, timeout_seconds) as db:
            snapshot.version = db.version()
            if query.schema:
                db.query("USE " + quote_identifier(query.schema))
            db.set_session(settings)
            if binding_policy not in {"exclude", "preserve"}:
                raise PCCError("INVALID_CONFIG", "Binding 策略无效")
            db.query("SET SESSION tidb_use_plan_baselines = %s", (1 if binding_policy == "preserve" else 0,))
            try:
                db.query("SET SESSION max_execution_time = %s", (timeout_seconds * 1000,))
                timeout_mode = "server_limit_and_control_connection"
            except PCCError:
                timeout_mode = "control_connection_with_read_timeout"
            snapshot.context = {"session": db.variables(), "binding_policy": binding_policy,
                                "timeout_mode": timeout_mode, "declared_session": settings,
                                "endpoint": {"host": profile["host"], "port": profile.get("port", 4000)},
                                "connection_id": db.scalar("SELECT CONNECTION_ID()"),
                                "production_reproduction": "unverified"}
            modes = str(snapshot.context["session"].get("sql_mode", "")).upper().split(",")
            if any(mode in {"ANSI_QUOTES", "NO_BACKSLASH_ESCAPES"} for mode in modes):
                raise PCCError("SQL_MODE_UNSUPPORTED", "目标 SQL mode 改变了守卫词法规则，需显式配置受支持的测试上下文")
            snapshot.context["server_node"] = db.records("SELECT @@hostname AS hostname, @@port AS port")
            statement = f"EXPLAIN FORMAT='{format}' " + query.sql_text
            snapshot.context["capture_mode"] = "prepared_explain" if query.parameters is not None else "text_explain"
            snapshot.context["prepared_plan_cache"] = "not_reproduced"
            for attempt in range(stats_retries + 1):
                if cancel.is_set():
                    snapshot.capture_status = "skipped"
                    break
                finished = threading.Event()
                timeout = threading.Event()
                connection_id = int(snapshot.context["connection_id"])

                def monitor():
                    deadline = time.monotonic() + timeout_seconds
                    while not finished.wait(0.1):
                        if not cancel.is_set() and time.monotonic() < deadline:
                            continue
                        timeout.set()
                        snapshot.cancellation = "pending"
                        try:
                            with db_factory(profile, 5) as control:
                                if finished.is_set():
                                    snapshot.cancellation = "query_finished"
                                    return
                                control.query(f"KILL TIDB QUERY {connection_id}")
                                for _ in range(10):
                                    rows = control.records("SELECT ID, COMMAND FROM information_schema.CLUSTER_PROCESSLIST WHERE ID = %s", (connection_id,))
                                    if not rows or all(str(row.get("COMMAND", "")).lower() == "sleep" for row in rows):
                                        snapshot.cancellation = "confirmed"
                                        return
                                    if finished.wait(0.1):
                                        # Read server state again; local completion alone is not confirmation.
                                        continue
                                snapshot.cancellation = "unknown"
                        except PCCError:
                            snapshot.cancellation = "timeout_only"
                        return

                thread = threading.Thread(target=monitor, daemon=True)
                thread.start()
                def check_cancelled():
                    if cancel.is_set() or timeout.is_set():
                        raise PCCError("CANCELLED" if cancel.is_set() else "EXPLAIN_TIMEOUT", "计划捕获已取消或超时")
                try:
                    if query.parameters is not None and attempt == 0:
                        statement, warnings = prepare_explain(db, query.sql_text, query.parameters, format, check_cancelled)
                        snapshot.context["prepare_warnings"] = warnings
                    check_cancelled()
                    snapshot.columns, snapshot.rows = db.query(statement)
                    finished.set()
                    # This must be the very next command on this connection.
                    snapshot.warnings = db.records("SHOW WARNINGS")
                finally:
                    finished.set()
                    thread.join(timeout=12)
                    if thread.is_alive():
                        snapshot.issues.append({"code": "CANCELLATION_PENDING", "message": "控制连接尚未确认服务端结束"})
                if timeout.is_set():
                    snapshot.capture_status = "failed"
                    snapshot.issues.append({"code": "CANCELLED" if cancel.is_set() else "EXPLAIN_TIMEOUT",
                                            "cancellation": snapshot.cancellation})
                    break
                snapshot.attempts.append({"attempt": attempt + 1, "captured_at": now(), "columns": snapshot.columns,
                                          "rows": snapshot.rows, "warnings": snapshot.warnings})
                raw = str(snapshot.rows).lower()
                warning_text = str(snapshot.warnings).lower()
                pseudo = "stats:pseudo" in raw or "stats: pseudo" in raw
                not_ready = pseudo or ("stats" in warning_text and any(w in warning_text for w in ("timeout", "not loaded", "pseudo")))
                if not not_ready:
                    break
                if attempt == stats_retries:
                    snapshot.issues.append({"code": "STATS_NOT_READY", "message": "EXPLAIN 使用 pseudo 或统计加载不完整"})
                elif cancel.wait(min(attempt + 1, 3)):
                    snapshot.capture_status = "skipped"
                    break
            snapshot.context["session_after"] = db.variables()
            if snapshot.context["session"] != snapshot.context["session_after"]:
                snapshot.issues.append({"code": "CONTEXT_DRIFT"})
    except PCCError as exc:
        snapshot.capture_status = "failed"
        # TiDB normally raises 1317 after KILL; retain the cancellation outcome
        # even when EXPLAIN exits through the driver-error path.
        if snapshot.cancellation and not any(i.get("code") in {"CANCELLED", "EXPLAIN_TIMEOUT"} for i in snapshot.issues):
            snapshot.issues.append({"code": "CANCELLED" if cancel.is_set() else "EXPLAIN_TIMEOUT",
                                    "cancellation": snapshot.cancellation})
        snapshot.issues.append({"code": exc.code, "message": exc.message})
    snapshot.captured_at = now()
    return snapshot
