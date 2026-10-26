"""Read the documented 19-column HEX TSV; never execute legacy scripts."""
from datetime import datetime
from pathlib import Path

from pcc_tool.models import PCCError
from .summary import number

COLUMNS = ("source_cluster", "collected_at", "closed_observed", "instance", "summary_begin_time",
           "summary_end_time", "stmt_type", "digest_text", "query_sample_text", "prev_sample_text",
           "prev_sample_digest", "sql_digest", "schema_name", "table_names", "plan_digest",
           "exec_count", "sum_latency", "max_latency", "min_latency")
HEX_COLUMNS = {0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14}


def read_tsv(path: Path, timezone: str) -> list[dict]:
    from zoneinfo import ZoneInfo
    rows = []
    for line_number, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        fields = line.split("\t")
        try:
            if len(fields) != 19:
                raise ValueError
            decoded = [bytes.fromhex(v).decode("utf-8") if i in HEX_COLUMNS and v not in {"NULL", "\\N"}
                       else (None if v in {"NULL", "\\N"} else v) for i, v in enumerate(fields)]
            row = dict(zip(COLUMNS, decoded))
            for key in ("collected_at", "summary_begin_time", "summary_end_time"):
                value = datetime.fromisoformat(row[key])
                row[key] = value.replace(tzinfo=ZoneInfo(timezone)).isoformat() if value.tzinfo is None else value.isoformat()
            for key in ("exec_count", "sum_latency", "max_latency", "min_latency"):
                number(row[key])
            if row["closed_observed"] not in {"0", "1"}:
                raise ValueError
            row.update(source_epoch="unknown", key_quality="lossy", avg_total_keys=None,
                       closed_observed=row["closed_observed"] == "1", format="legacy_hex_tsv_19")
            rows.append(row)
        except (ValueError, TypeError, UnicodeError):
            raise PCCError("LEGACY_INVALID", f"旧 TSV 第 {line_number} 行不符合 19 列契约") from None
    return rows


def import_materials(source: Path, destination: Path, queries):
    """Adapt the documented legacy filenames; never infer SQL from selection scripts."""
    import json
    import re
    import sqlglot
    from sqlglot import exp
    from pcc_tool.artifacts.store import safe_path, write_bytes
    from pcc_tool.capture.guard import dependencies
    from pcc_tool.collector.dependencies import ddl_signature
    from pcc_tool.models import byte_hash, digest
    databases, objects, issues, visiting = {}, {}, [], set()

    def visit(schema, table):
        key = (schema, table)
        if key in objects:
            return
        if key in visiting:
            raise PCCError("DEPENDENCY_UNSUPPORTED", "旧包视图依赖循环")
        visiting.add(key)
        try:
            if any("." in v or "/" in v or "\\" in v for v in key):
                raise PCCError("LEGACY_INVALID", "旧文件命名无法无歧义表示该对象")
            original = safe_path(source, f"metadata_backup/{schema}.{table}.sql")
            ddl = original.read_text("utf-8")
            statements = [s for s in sqlglot.parse(ddl, read="mysql") if s is not None and not isinstance(s, exp.Semicolon)]
            if len(statements) != 1 or not isinstance(statements[0], exp.Create):
                raise PCCError("LEGACY_INVALID", "旧 DDL 必须是单条 CREATE")
            create = statements[0]
            name = create.this.this if isinstance(create.this, exp.Schema) else create.this
            kind = str(create.args.get("kind", "")).lower()
            if kind not in {"table", "view"} or not isinstance(name, exp.Table) or name.name != table or name.db not in {"", schema}:
                raise PCCError("LEGACY_INVALID", "旧 DDL 对象名不匹配")
            if re.search(r"\b(?:FOREIGN\s+KEY|PLACEMENT\s+POLICY|NEXTVAL|SEQUENCE|TEMPORARY)\b", ddl, re.I):
                raise PCCError("DEPENDENCY_UNSUPPORTED", "旧包包含尚不支持的依赖")
            deps = dependencies(create.args["expression"], schema) if kind == "view" else []
            for pair in deps:
                visit(*pair)
            if schema not in databases:
                original_db = safe_path(source, f"metadata_backup/{schema}-schema-create.sql")
                database_ddl = original_db.read_bytes()
                filename = f"metadata_backup/database-{digest(schema)}.sql"
                write_bytes(destination / filename, database_ddl)
                databases[schema] = {"schema": schema, "ddl_file": filename, "ddl_hash": ddl_signature(database_ddl.decode())}
            filename = f"metadata_backup/object-{digest(key)}.sql"
            write_bytes(destination / filename, ddl.encode())
            obj = {"schema": schema, "table": table, "kind": kind, "ddl_file": filename, "ddl_hash": ddl_signature(ddl),
                   "dependencies": deps, "stats_file": None, "stats_ready": False}
            objects[key] = obj
            if kind == "table":
                stats_file = safe_path(source, f"statistics_backup/{schema}.{table}.json")
                raw = stats_file.read_bytes()
                stats = json.loads(raw)
                if not isinstance(stats, dict) or stats.get("database_name") != schema or stats.get("table_name") != table:
                    raise PCCError("STATS_OBJECT_MISMATCH", "旧统计文件对象不匹配")
                filename = f"statistics_backup/{digest(key)}.json"
                write_bytes(destination / filename, raw)
                obj.update(stats_file=filename, stats_hash=byte_hash(raw), stats_ready=True,
                           stats_captured_at="unknown", stats_verification="source_histogram_evidence_unavailable")
                issues.append({"code": "STATS_VERIFICATION_BASELINE_UNKNOWN", "object": list(key),
                               "message": "旧包缺少源统计核验明细；请重新从源端导出材料后完成比较"})
        except PCCError as exc:
            issues.append({"code": exc.code, "message": exc.message, "object": list(key)})
        except (OSError, ValueError, TypeError, KeyError, sqlglot.errors.SqlglotError):
            issues.append({"code": "LEGACY_INVALID", "object": list(key), "message": "旧包材料缺失或格式无效"})
        finally:
            visiting.discard(key)
    for query in queries:
        for pair in query.dependencies:
            visit(*pair)
    return list(databases.values()), list(objects.values()), issues
