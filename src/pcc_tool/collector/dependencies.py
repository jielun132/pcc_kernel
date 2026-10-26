from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import sqlglot
from sqlglot import exp

from pcc_tool.artifacts.store import safe_path, write_bytes
from pcc_tool.capture.connection import Database, status_json, verify_status_identity
from pcc_tool.capture.guard import dependencies, quote_identifier
from pcc_tool.models import PCCError, QueryCase, byte_hash, canonical, digest, now


def qualified(schema, table):
    return quote_identifier(schema) + "." + quote_identifier(table)


def ddl_signature(ddl: str) -> str:
    # Collapse whitespace only outside literals/identifiers. Never erase index, partition or literal data.
    tokens = re.findall(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|`(?:``|[^`])*`|\S+", ddl)
    return digest(" ".join(tokens).rstrip(";"))


def ddl_semantic_signature(ddl: str) -> str:
    """Ignore integer display widths removed from SHOW CREATE since TiDB 8.5.

    Only quoted column declarations are eligible. Preserve ZEROFILL, all other
    lengths/precisions, literals, comments and the original exported DDL bytes.
    Token offsets avoid replacing type-looking text inside strings or comments.
    """
    from sqlglot.tokens import TokenType as T
    try:
        tokens = sqlglot.tokenize(ddl, read="mysql")
    except sqlglot.errors.SqlglotError:
        return ddl_signature(ddl)
    cuts = []
    for i, token in enumerate(tokens):
        if token.text.lower() not in {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"} or i < 2 or i + 3 >= len(tokens):
            continue
        if (tokens[i - 1].token_type != T.IDENTIFIER or tokens[i - 2].token_type not in {T.L_PAREN, T.COMMA}
                or [t.token_type for t in tokens[i + 1:i + 4]] != [T.L_PAREN, T.NUMBER, T.R_PAREN]):
            continue
        suffix = []
        for following in tokens[i + 4:]:
            if following.token_type in {T.COMMA, T.R_PAREN}:
                break
            suffix.append(following.text.lower())
        if "zerofill" in suffix:
            continue
        start, end = tokens[i + 1].start, tokens[i + 3].end + 1
        if re.fullmatch(r"\(\s*\d+\s*\)", ddl[start:end]):
            cuts.append((start, end))
    for start, end in reversed(cuts):
        ddl = ddl[:start] + ddl[end:]
    return ddl_signature(ddl)


def show_create(db: Database, schema: str, table: str | None = None) -> str:
    rows = db.query(f"SHOW CREATE {'TABLE ' + qualified(schema, table) if table else 'DATABASE ' + quote_identifier(schema)}")[1]
    if len(rows) != 1 or len(rows[0]) < 2:
        raise PCCError("DEPENDENCY_MISSING", "SHOW CREATE 未返回对象定义")
    return rows[0][1]


def stats_evidence(db: Database, schema: str, table: str) -> dict:
    meta = db.records("SHOW STATS_META WHERE Db_name = %s AND Table_name = %s", (schema, table))
    hist = db.records("SHOW STATS_HISTOGRAMS WHERE Db_name = %s AND Table_name = %s", (schema, table))
    stable_meta = [{k: v for k, v in row.items() if k.lower() not in {"update_time", "last_analyze_time"}} for row in meta]
    stable_hist = [{k: v for k, v in row.items() if k.lower() in {
        "db_name", "table_name", "partition_name", "column_name", "is_index", "distinct_count", "null_count"}} for row in hist]
    return {"meta": sorted(stable_meta, key=canonical), "histograms": sorted(stable_hist, key=canonical)}


def stats_fingerprint(stats: dict) -> str:
    """Compare optimizer statistics, excluding documented collection/usage metadata.

    Keep every histogram bucket, TopN/sketch value, count, size, correlation and stats
    version. Predicate-column usage timestamps/order and TSO update versions do not
    describe the captured data distribution. The original JSON remains unchanged.
    """
    value = {k: v for k, v in stats.items() if k not in {"version", "predicate_columns", "is_historical_stats"}}
    for group in ("columns", "indices"):
        if isinstance(value.get(group), dict):
            value[group] = {name: {k: v for k, v in column.items() if k != "last_update_version"}
                            for name, column in value[group].items()}
            for column in value[group].values():
                sketch = column.get("fm_sketch")
                if isinstance(sketch, dict) and isinstance(sketch.get("hashset"), list):
                    # Go map iteration changes this set's JSON order on every dump.
                    # Preserve all values and other sketch fields; do not sort buckets.
                    column["fm_sketch"] = {**sketch, "hashset": sorted(sketch["hashset"], key=canonical)}
    if isinstance(value.get("partitions"), dict):
        value["partitions"] = {name: stats_fingerprint(part) for name, part in value["partitions"].items()}
    return digest(value)


def export_dependencies(db: Database, queries: list[QueryCase], root: Path):
    objects, databases, issues, visiting = {}, {}, [], set()
    identity = None
    try:
        identity = verify_status_identity(db, db.profile)
    except PCCError as exc:
        issues.append({"code": exc.code, "message": exc.message})

    def visit(schema, table):
        key = (schema, table)
        if key in objects:
            return
        if key in visiting:
            raise PCCError("DEPENDENCY_UNSUPPORTED", "视图依赖循环")
        visiting.add(key)
        try:
            if schema not in databases:
                ddl = show_create(db, schema)
                name = "metadata_backup/database-" + digest(schema) + ".sql"
                write_bytes(root / name, (ddl.rstrip(";") + ";\n").encode())
                databases[schema] = {"schema": schema, "ddl_file": name, "ddl_hash": ddl_signature(ddl)}
            ddl = show_create(db, schema, table)
            if re.search(r"\b(?:FOREIGN\s+KEY|PLACEMENT\s+POLICY|NEXTVAL|SEQUENCE|TEMPORARY)\b", ddl, re.I):
                raise PCCError("DEPENDENCY_UNSUPPORTED", "外键、Placement Policy、Sequence 或临时表依赖尚不支持")
            table_type = db.scalar("SELECT TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (schema, table))
            if table_type not in {"VIEW", "BASE TABLE"}:
                raise PCCError("DEPENDENCY_UNSUPPORTED", "对象不是受支持的普通表或视图")
            kind = "view" if table_type == "VIEW" else "table"
            deps = []
            if kind == "view":
                try:
                    tree = sqlglot.parse_one(ddl, read="mysql")
                    select = tree.args.get("expression")
                    if not isinstance(select, (exp.Select, exp.SetOperation)):
                        raise ValueError
                    deps = dependencies(select, schema)
                except Exception:
                    raise PCCError("DEPENDENCY_UNSUPPORTED", "视图定义无法完整解析") from None
                for dep in deps:
                    visit(*dep)
            token = digest(key)
            ddl_file = "metadata_backup/object-" + token + ".sql"
            write_bytes(root / ddl_file, (ddl.rstrip(";") + ";\n").encode())
            obj = {"schema": schema, "table": table, "kind": kind, "dependencies": deps,
                   "ddl_file": ddl_file, "ddl_hash": ddl_signature(ddl),
                   "ddl_semantic_hash": ddl_semantic_signature(ddl), "ddl_captured_at": now(),
                   "stats_file": None, "stats_ready": False}
            objects[key] = obj
            if kind == "table":
                try:
                    if identity is None:
                        raise PCCError("STATUS_IDENTITY_UNVERIFIED", "SQL 与 Status 同源身份尚未确认")
                    before_stats = stats_evidence(db, schema, table)
                    raw = status_json(db.profile, ("stats", "dump", schema, table), raw=True)
                    stats = json.loads(raw, parse_float=Decimal)
                    if stats.get("database_name") != schema or stats.get("table_name") != table:
                        raise PCCError("STATS_OBJECT_MISMATCH", "统计 JSON 对象名与请求不一致")
                    if not any(k in stats for k in ("columns", "indices", "partitions")):
                        raise PCCError("STATS_MISSING", "统计 JSON 缺少列、索引及分区信息")
                    stats_file = "statistics_backup/" + token + ".json"
                    write_bytes(root / stats_file, raw)
                    after_stats = stats_evidence(db, schema, table)
                    obj.update(stats_file=stats_file, stats_hash=byte_hash(raw), stats_fingerprint=stats_fingerprint(stats), stats_captured_at=now(),
                               stats_evidence=after_stats, stats_ready=True)
                    after_dump = status_json(db.profile, ("stats", "dump", schema, table))
                    if before_stats["meta"] != after_stats["meta"] or stats_fingerprint(after_dump) != obj["stats_fingerprint"]:
                        issues.append({"code": "STATS_DRIFT", "object": list(key), "message": "导出统计期间源统计发生变化"})
                except PCCError as exc:
                    issues.append({"code": exc.code, "message": exc.message, "object": list(key)})
        except PCCError as exc:
            issues.append({"code": exc.code, "message": exc.message, "object": list(key)})
        finally:
            visiting.discard(key)

    for q in queries:
        for pair in q.dependencies:
            visit(*pair)
    for key, obj in objects.items():
        try:
            if ddl_signature(show_create(db, *key)) != obj["ddl_hash"]:
                issues.append({"code": "SCHEMA_DRIFT", "object": list(key), "message": "导出期间表结构发生变化"})
        except PCCError as exc:
            issues.append({"code": exc.code, "object": list(key), "message": exc.message})
    try:
        bindings = binding_evidence(db.records("SHOW GLOBAL BINDINGS"))
    except PCCError:
        bindings = None
        issues.append({"code": "BINDINGS_UNKNOWN", "severity": "warning"})
    try:
        tiflash = db.records("SELECT * FROM information_schema.TIFLASH_REPLICA")
        wanted = set(objects)
        tiflash = [row for row in tiflash if (row.get("TABLE_SCHEMA"), row.get("TABLE_NAME")) in wanted]
    except PCCError:
        tiflash = None
        issues.append({"code": "TIFLASH_VISIBILITY_UNKNOWN", "severity": "warning"})
    context = {"version": db.version(), "capture_session": db.variables(), "historical_session": "unknown",
               "status_identity": identity, "bindings": bindings, "tiflash": tiflash, "captured_at": now(),
               "production_reproduction": "unverified", "stats_time_semantics": "export_time_not_business_window"}
    return list(databases.values()), list(objects.values()), context, issues


def verify_materials(db: Database, manifest: dict) -> tuple[dict, list[dict]]:
    state, issues = {}, []
    use_status = bool(getattr(db, "profile", {}).get("status_url"))
    if use_status:
        try:
            verify_status_identity(db, db.profile)
        except PCCError as exc:
            return {}, [{"code": exc.code, "message": exc.message}]
    for obj in manifest.get("objects", []):
        key = (obj["schema"], obj["table"])
        identity = digest(key)
        try:
            ddl = show_create(db, *key)
            ddl_hash = ddl_signature(ddl)
            state[identity] = {"object": list(key), "ddl_hash": ddl_hash}
            matches = (ddl_semantic_signature(ddl) == obj["ddl_semantic_hash"] if obj.get("ddl_semantic_hash")
                       else ddl_hash == obj["ddl_hash"])
            if not matches:
                issues.append({"code": "SCHEMA_MISMATCH", "object": list(key)})
            if obj["kind"] == "table":
                if use_status and obj.get("stats_fingerprint"):
                    actual_dump = status_json(db.profile, ("stats", "dump", *key))
                    actual_hash = stats_fingerprint(actual_dump)
                    state[identity]["stats"] = {"fingerprint": actual_hash}
                    if (not obj.get("stats_ready") or actual_hash != obj["stats_fingerprint"]
                            or actual_dump.get("database_name") != key[0] or actual_dump.get("table_name") != key[1]):
                        issues.append({"code": "STATS_NOT_READY", "object": list(key),
                                       "message": "持久化统计内容与来源不一致"})
                    continue
                actual = stats_evidence(db, *key)
                state[identity]["stats"] = actual
                if not obj.get("stats_ready") or not actual["meta"] or actual != obj.get("stats_evidence"):
                    issues.append({"code": "STATS_NOT_READY", "object": list(key),
                                   "message": "表、分区或索引统计尚未与来源证据一致"})
        except PCCError as exc:
            issues.append({"code": "MATERIAL_VERIFICATION_FAILED", "object": list(key), "cause": exc.code})
    return state, issues


def binding_evidence(rows):
    return sorted([{k: v for k, v in row.items() if k.lower() not in {"create_time", "update_time"}} for row in rows], key=canonical)


def verify_tiflash(db: Database, expected):
    if not expected:
        return [], []
    try:
        rows = db.records("SELECT * FROM information_schema.TIFLASH_REPLICA")
    except PCCError as exc:
        return [], [{"code": "TIFLASH_NOT_READY", "cause": exc.code}]
    state, issues = [], []
    for source in expected:
        key = [source.get("TABLE_SCHEMA"), source.get("TABLE_NAME")]
        matching = [r for r in rows if [r.get("TABLE_SCHEMA"), r.get("TABLE_NAME")] == key]
        ready = len(matching) == 1 and str(matching[0].get("AVAILABLE")) == "1" and str(matching[0].get("REPLICA_COUNT")) == str(source.get("REPLICA_COUNT"))
        state.append({"object": key, "ready": ready})
        if not ready:
            issues.append({"code": "TIFLASH_NOT_READY", "object": key})
    return state, issues
