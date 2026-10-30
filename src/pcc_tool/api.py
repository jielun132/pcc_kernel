"""Public SDK. CLI is a thin adapter around these same functions."""
from __future__ import annotations

import dataclasses
import platform
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from pcc_tool.artifacts.store import (Publication, load_bundle, read_json, read_jsonl, safe_path,
                                     source_lock, verify, write_bytes, write_json, write_jsonl)
from pcc_tool.capture.connection import Database, profile_named
from pcc_tool.capture.explain import capture_plan, negotiate_format
from pcc_tool.collector.dependencies import binding_evidence, export_dependencies, qualified, verify_materials, verify_tiflash
from pcc_tool.collector.legacy import read_tsv
from pcc_tool.collector.source import read_repository, read_summary
from pcc_tool.collector.spool import publish_batch, read_spool
from pcc_tool.collector.summary import make_query, select_queries
from pcc_tool.models import (CONTRACT_VERSION, VERSIONS, CaptureBundle, CollectRequest, CompareRequest,
                            ComparisonResult, DiffRequest, DoctorRequest, ExportRequest, PCCError,
                            PlanSnapshot, PrepareRequest, QueryCase, business_value, canonical, digest, now, primitive)
from pcc_tool.plan.diff import compare_ir
from pcc_tool.plan.parser import parse_plan
from pcc_tool.render.report import render_reports


class Events:
    def __init__(self, callback=None):
        self.callback, self.rows = callback, []

    def emit(self, event, **data):
        row = {"contract_version": CONTRACT_VERSION, "event": event, "sequence": str(len(self.rows) + 1),
               "time": now(), **primitive(data)}
        self.rows.append(row)
        if self.callback:
            self.callback(row)


def _query_file(path: str, allowed):
    file = Path(path)
    if file.suffix == ".jsonl":
        rows = read_jsonl(file)
        queries = []
        for i, row in enumerate(rows, 1):
            q = make_query(row.get("sql_text"), row.get("schema", ""), {"file": file.name, "row": i},
                           row.get("context", {}), allowed, row.get("truncated", False), parameters=row.get("parameters"))
            for key in ("source_digest", "source_cluster"):
                value = row.get(key, "")
                if not isinstance(value, str):
                    raise PCCError("SQL_FILE_INVALID", "SQL 样例的来源标识必须是字符串")
                setattr(q, key, value)
            queries.append(q)
        return queries
    # Token offsets preserve original bytes, hints and literals when splitting an input script.
    import sqlglot
    from sqlglot.tokens import TokenType
    sql = file.read_bytes().decode("utf-8")
    try:
        ends = [token.end + 1 for token in sqlglot.tokenize(sql, read="mysql") if token.token_type == TokenType.SEMICOLON]
    except Exception:
        raise PCCError("SQL_FILE_INVALID", "SQL 文件无法完整分词") from None
    parts, start = [], 0
    for end in ends + [len(sql)]:
        text = sql[start:end]
        if text.strip():
            parts.append(make_query(text, origin={"file": file.name, "offset": start}, allowed=allowed))
        start = end
    return parts


def export(request: ExportRequest, *, profiles=None, on_event=None, cancel=None) -> CaptureBundle:
    profiles, events = profiles or {}, Events(on_event)
    selectors = [request.from_summary, request.spool, request.repository, request.legacy_package, request.sql_file]
    if sum(bool(s) for s in selectors) != 1:
        raise PCCError("INVALID_CONFIG", "export 必须且只能选择一个输入来源")
    issues, selection, metadata = [], {}, {}
    inherited, legacy_root = None, None
    if request.legacy_package:
        root = Path(request.legacy_package)
        if (root / "manifest.json").exists():
            inherited = load_bundle(str(root))
            queries, selection = inherited.queries, inherited.selection
        else:
            legacy_root = root
            candidates = [root / "queries.jsonl", root / "sql_samples.jsonl"]
            sample_file = next((p for p in candidates if p.is_file()), None)
            if not sample_file:
                raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "旧包必须补充 queries.jsonl；top_sql_selection.sql 不是业务 SQL")
            queries = _query_file(str(sample_file), request.statement_types)
            selection = {"kind": "legacy_sample_set", "ranking": "not_applied"}
    elif request.sql_file:
        queries = _query_file(request.sql_file, request.statement_types)
        selection = {"kind": "explicit_samples", "ranking": "not_applied"}
    else:
        if not request.begin or not request.end:
            raise PCCError("INVALID_CONFIG", "指标筛选必须指定 begin 和 end")
        if request.from_summary:
            with Database(profile_named(profiles, request.source)) as db:
                rows, metadata = read_summary(db, request.source, request.begin, request.end)
        elif request.spool:
            if Path(request.spool).is_file():
                if not request.source:
                    raise PCCError("INVALID_CONFIG", "旧 TSV 读取需通过 source 配置时区")
                rows = read_tsv(Path(request.spool), profile_named(profiles, request.source).get("timezone", "UTC"))
            else:
                rows, metadata = read_spool(request.spool)
        else:
            if not request.source:
                raise PCCError("INVALID_CONFIG", "repository 需要 source_cluster 过滤来源")
            with Database(profile_named(profiles, request.repository)) as db:
                cluster = profiles.get(request.source, {}).get("source_cluster", request.source)
                rows = read_repository(db, cluster, request.begin, request.end)
        queries, selection, issues = select_queries(rows, begin=request.begin, end=request.end, top_n=request.top_n,
            order_by=request.order_by, ranking_scope=request.ranking_scope, schemas=request.schemas,
            statement_types=request.statement_types)
    dedup = {}
    for q in queries:
        if q.query_id in dedup:
            dedup[q.query_id].sample_origins.extend(q.sample_origins)
        else:
            dedup[q.query_id] = q
    queries = list(dedup.values())
    if not queries:
        raise PCCError("NO_QUERIES_SELECTED", "没有入选样例")
    if cancel and cancel.is_set():
        raise PCCError("INTERRUPTED", "导出已取消")
    if request.dependencies_bundle:
        inherited = load_bundle(request.dependencies_bundle)
    fingerprint = digest({"queries": queries, "selection": selection, "versions": VERSIONS})
    events.emit("export_started", query_count=len(queries))
    with Publication(request.out, fingerprint) as publication:
        root = publication.work
        if inherited:
            objects, databases, context = inherited.manifest.get("objects", []), inherited.manifest.get("databases", []), inherited.context
            for name in inherited.manifest["files"]:
                if name.startswith(("metadata_backup/", "statistics_backup/")):
                    write_bytes(root / name, safe_path(Path(inherited.path), name).read_bytes())
            issues.extend(inherited.issues)
        elif request.source:
            with Database(profile_named(profiles, request.source)) as db:
                databases, objects, context, dependency_issues = export_dependencies(db, queries, root)
                issues.extend(dependency_issues)
        elif legacy_root:
            from pcc_tool.collector.legacy import import_materials
            databases, objects, dependency_issues = import_materials(legacy_root, root, queries)
            context = {"historical_session": "unknown", "production_reproduction": "unverified", "legacy_materials": True}
            issues.extend(dependency_issues)
        else:
            databases, objects, context = [], [], {"historical_session": "unknown", "production_reproduction": "unverified"}
        known = {(o["schema"], o["table"]) for o in objects}
        for q in queries:
            issues.extend(q.issues)
            for pair in q.dependencies:
                if tuple(pair) not in known:
                    issues.append({"code": "DEPENDENCY_MISSING", "query_id": q.query_id, "object": pair})
        context = {**context, "collection": metadata}
        manifest = {"kind": "CaptureBundle", "versions": VERSIONS, "created_at": now(), "objects": objects,
                    "databases": databases, "run_status": "partial" if issues else "completed",
                    "source": request.source, "query_count": str(len(queries))}
        write_jsonl(root / "queries.jsonl", queries)
        write_json(root / "context.json", context)
        write_json(root / "selection.json", selection)
        write_jsonl(root / "issues.jsonl", issues)
        write_bytes(root / "table_list.txt", "".join(qualified(o["schema"], o["table"]) + "\n" for o in objects).encode())
        write_bytes(root / "skipped_tables.txt", "".join(canonical(i) + "\n" for i in issues if i.get("object")).encode())
        publication.checkpoint()
        publication.publish(manifest)
    events.emit("export_completed", output_dir=request.out, run_status=manifest["run_status"])
    return load_bundle(request.out)


def collect(request: CollectRequest, *, profiles=None, on_event=None, cancel=None) -> dict:
    if request.interval_seconds < 1:
        raise PCCError("INVALID_CONFIG", "采集间隔必须为正数")
    profile = profile_named(profiles or {}, request.source)
    events, cancel = Events(on_event), cancel or threading.Event()
    root = Path(request.out)
    count = 0
    with source_lock(root / ".source.lock"):
        source_file = root / "source.json"
        identity = {"source_cluster": profile.get("source_cluster", request.source)}
        if source_file.exists() and read_json(source_file) != identity:
            raise PCCError("SOURCE_MISMATCH", "持久化目录已属于其他源集群")
        write_json(source_file, identity)
        while not cancel.is_set():
            with Database(profile) as db:
                rows, metadata = read_summary(db, request.source)
            path = publish_batch(root, request.source, rows, metadata)
            count += 1
            events.emit("batch_published", file=path.name, rows=len(rows), issues=metadata.get("issues", []))
            if not request.watch or cancel.wait(request.interval_seconds):
                break
    return {"run_status": "interrupted" if cancel.is_set() else "completed", "batches": str(count)}


def prepare(request: PrepareRequest, *, profiles=None, on_event=None) -> dict:
    bundle = load_bundle(request.bundle)
    if not request.targets or len(set(request.targets)) != len(request.targets):
        raise PCCError("INVALID_CONFIG", "目标名称不能为空或重复")
    for target in request.targets:
        if not target.isidentifier():
            raise PCCError("INVALID_CONFIG", "目标名称必须为标识符")
        if profiles:
            profile_named(profiles, target)
    fp = digest({"manifest": bundle.manifest, "targets": request.targets, "versions": VERSIONS})
    with Publication(request.out, fp) as publication:
        root = publication.work
        for name in bundle.manifest["files"]:
            if name.startswith(("metadata_backup/", "statistics_backup/")):
                write_bytes(root / "materials" / name, safe_path(Path(bundle.path), name).read_bytes())
        for target in request.targets:
            script = ["-- DBA 审核后执行。不会 DROP/TRUNCATE，也不会 ANALYZE。", "-- 先执行 verification.sql；已有对象必须比对结构并跳过对应 CREATE。",
                      "-- LOAD STATS 路径和 local infile 传输行为必须在指定 TiDB/MySQL 客户端版本核验。"]
            checks = []
            for obj in bundle.manifest.get("databases", []):
                script.append((root / "materials" / obj["ddl_file"]).read_text("utf-8"))
            for obj in bundle.manifest.get("objects", []):
                script.append("USE `" + obj["schema"].replace("`", "``") + "`;")
                script.append((root / "materials" / obj["ddl_file"]).read_text("utf-8"))
                checks.extend([f"SHOW CREATE TABLE {qualified(obj['schema'], obj['table'])};"])
                if obj.get("stats_file"):
                    path = (Path(request.out).absolute() / "materials" / obj["stats_file"]).as_posix()
                    script.append("LOAD STATS '" + path.replace("\\", "\\\\").replace("'", "''") + "';")
            checks.extend(["SHOW STATS_META;", "SHOW STATS_HISTOGRAMS;", "SHOW VARIABLES LIKE 'tidb_use_plan_baselines';"])
            write_bytes(root / target / "import_commands.sql", ("\n".join(script) + "\n").encode())
            write_bytes(root / target / "verification.sql", ("\n".join(checks) + "\n").encode())
            p = (profiles or {}).get(target, {})
            write_json(root / target / "plan.json", {"bundle_hash": digest(bundle.manifest), "target": target,
                       "identity": {k: p[k] for k in ("host", "port", "expected_version") if k in p},
                       "objects": bundle.manifest.get("objects", []), "issues": bundle.issues,
                       "execution": "manual_only", "materials_path": str(Path(request.out).absolute() / "materials")})
        publication.checkpoint()
        publication.publish({"kind": "Preparation", "run_status": "partial" if bundle.issues else "completed"})
    return {"output_dir": request.out, "targets": request.targets, "execution": "manual_only",
            "run_status": "partial" if bundle.issues else "completed"}


def _summarize(queries, snapshots, fingerprint, issues, interrupted=False):
    from pcc_tool.plan.findings import classify_findings
    items, diffs, irs = [], [], {}
    for q in queries:
        events = []
        item_issues = [i for i in issues if (not i.get("query_id") or i["query_id"] == q.query_id)
                       and (not i.get("object") or i["object"] in q.dependencies)]
        item = {"query_id": q.query_id, "compare_status": "not_run", "needs_sample": q.executable_status == "needs_sample",
                "requires_review": False, "production_reproduction": "unverified", "issues": item_issues}
        pair = []
        for side in ("old", "new"):
            snapshot = snapshots.get((q.query_id, side))
            item[side] = {"capture_status": snapshot.capture_status if snapshot else "skipped", "parse_status": "failed"}
            if snapshot:
                item_issues.extend({**i, "side": side, "query_id": q.query_id} for i in snapshot.issues)
                ir = parse_plan(snapshot)
                irs[(q.query_id, side)] = ir
                item[side]["parse_status"] = ir.parse_status
                item_issues.extend({**i, "side": side, "query_id": q.query_id} for i in ir.issues)
                pair.append((snapshot, ir))
        if q.executable_status == "ok" and len(pair) == 2 and all(p[0].capture_status != "skipped" for p in pair):
            item["compare_status"] = "incomparable"
            if all(p[0].capture_status == "ok" for p in pair):
                events, review = compare_ir(pair[0][1], pair[1][1])
                if any(e.type == "ambiguous_match" for e in events):
                    item_issues.append({"code": "PLAN_MATCH_AMBIGUOUS", "query_id": q.query_id,
                                        "message": "局部节点无法唯一对应，保留观察结果，不能完整比较"})
                diffs.extend(events)
                contexts = [p[0].context.get("session", {}) for p in pair]
                if contexts[0] != contexts[1]:
                    required_context = {"sql_mode", "time_zone", "collation_connection", "character_set_connection",
                                        "tidb_isolation_read_engines", "tidb_allow_mpp", "tidb_enforce_mpp", "tidb_use_plan_baselines"}
                    required_context.update(pair[0][0].context.get("declared_session", {}))
                    required_context.update(pair[1][0].context.get("declared_session", {}))
                    material_difference = any(contexts[0].get(k) != contexts[1].get(k) for k in required_context)
                    item_issues.append({"code": "CONTEXT_DIFFERENCE", "query_id": q.query_id,
                                        "severity": "error" if material_difference else "warning",
                                        "message": "两端上下文不同；只能报告计划事实，不能直接归因于版本性能回退",
                                        "old": contexts[0], "new": contexts[1]})
                blocking = [i for i in item_issues if i.get("severity", "error") != "warning"]
                if not review and not blocking:
                    item["compare_status"] = "changed" if events else "same"
                item["requires_review"] = review or bool(blocking)
        elif q.executable_status == "unsupported":
            item["requires_review"] = True
        # Pre/post checks and archived capture issues can repeat the same finding.
        # Treat findings as a stable set so offline replay preserves failed items
        # as well as successful ones, without multiplying or hiding any issue.
        unique = {digest(i): i for i in item_issues}
        item["issues"] = [unique[key] for key in sorted(unique)]
        item["result_category"] = item["compare_status"] if item["compare_status"] in {"same", "changed"} else "unavailable"
        for event in events:
            event.compare_status = item["compare_status"]
        item["findings"] = classify_findings(irs.get((q.query_id, "old")), irs.get((q.query_id, "new")),
                                             events, item["compare_status"])
        items.append(item)
    counts = {status: sum(i["compare_status"] == status for i in items) for status in ("same", "changed", "incomparable", "not_run")}
    selected = len(queries)
    full = counts["same"] + counts["changed"]
    from decimal import Decimal
    digests = {(o.get("source_cluster", q.source_cluster), o.get("schema_name", q.schema), o.get("sql_digest", q.source_digest))
               for q in queries for o in q.sample_origins if o.get("sql_digest", q.source_digest)}
    summary = {"digest_count": len(digests), "selected_samples": selected,
               "executable_samples": sum(q.executable_status == "ok" for q in queries),
               "both_captured": sum(all(i[s]["capture_status"] == "ok" for s in ("old", "new")) for i in items),
               "full_comparisons": full, **counts, "needs_sample": sum(i["needs_sample"] for i in items),
               "events": len(diffs), "complete_comparison_rate": str(Decimal(full) / Decimal(selected)) if selected else None,
               "coverage_denominator": "selected_samples_including_unresolved", "business_coverage": "unknown"}
    findings = [f for item in items for f in item["findings"]]
    summary.update(unavailable=counts["incomparable"] + counts["not_run"],
                   confirmed_events=sum(e.compare_status == "changed" for e in diffs),
                   unconfirmed_events=sum(e.compare_status != "changed" for e in diffs),
                   plan_findings=sum(f["confirmed"] and f["kind"] == "plan" for f in findings),
                   auxiliary_findings=sum(f["confirmed"] and f["kind"] == "auxiliary" for f in findings))
    status = "interrupted" if interrupted else "blocked" if not selected else "partial" if full != selected else "completed"
    if not selected:
        issues = [*issues, {"code": "NO_QUERIES_SELECTED"}]
    all_issues = list(issues)
    for item in items:
        all_issues.extend(item["issues"])
    unique_issues = {digest(i): i for i in all_issues}
    return ComparisonResult(fingerprint, primitive(summary), items, diffs, list(unique_issues.values()), status), irs


def _write_result(root, result, bundle, snapshots, irs, events):
    from pcc_tool.plan.findings import similar_groups
    write_json(root / "summary.json", result.summary)
    write_jsonl(root / "items.jsonl", result.items)
    write_jsonl(root / "diffs.jsonl", result.diffs)
    write_jsonl(root / "issues.jsonl", result.issues)
    write_jsonl(root / "findings.jsonl", [f for item in result.items for f in item.get("findings", [])])
    write_json(root / "similar_groups.json", similar_groups(result.items))
    write_jsonl(root / "inputs/queries.jsonl", bundle.queries)
    write_json(root / "inputs/context.json", bundle.context)
    write_json(root / "inputs/selection.json", bundle.selection)
    write_json(root / "inputs/manifest.json", bundle.manifest)
    write_jsonl(root / "inputs/issues.jsonl", bundle.issues)
    for side in ("old", "new"):
        write_jsonl(root / f"plans/{side}.jsonl", [snapshots[q.query_id, side] for q in bundle.queries if (q.query_id, side) in snapshots])
        write_jsonl(root / f"ir/{side}.jsonl", [irs[q.query_id, side] for q in bundle.queries if (q.query_id, side) in irs])
    write_jsonl(root / "events.jsonl", events.rows)
    render_reports(root, result, bundle.queries, snapshots, irs, bundle.selection, bundle.context)


def _bundle_business(bundle):
    return business_value({"queries": bundle.queries, "context": bundle.context, "selection": bundle.selection,
                           "objects": bundle.manifest.get("objects", []), "databases": bundle.manifest.get("databases", [])})


def compare(request: CompareRequest, *, profiles=None, on_event=None, cancel=None) -> ComparisonResult:
    profiles, cancel, events = profiles or {}, cancel or threading.Event(), Events(on_event)
    if (any(type(v) is not int for v in (request.concurrency, request.timeout_seconds, request.stats_retries))
            or not 1 <= request.concurrency <= 16 or not 1 <= request.timeout_seconds <= 300 or not 0 <= request.stats_retries <= 5):
        raise PCCError("INVALID_CONFIG", "并发、超时或重试次数超出范围")
    if request.old == request.new:
        raise PCCError("INVALID_CONFIG", "old 和 new 必须是两套独立目标")
    bundle = load_bundle(request.bundle)
    target_profiles = {"old": profile_named(profiles, request.old), "new": profile_named(profiles, request.new)}
    if any(p.get("role") != "test" for p in target_profiles.values()):
        raise PCCError("TARGET_NOT_ISOLATED", "compare 只连接配置中明确 role=test 的隔离环境")
    endpoints = [(p["host"], int(p.get("port", 4000))) for p in target_profiles.values()]
    if endpoints[0] == endpoints[1]:
        raise PCCError("TARGET_NOT_ISOLATED", "old 和 new 不能是同一个 SQL 端点")
    target_info, pre_issues, before, supported = {}, list(bundle.issues), {}, {}
    for side, profile in target_profiles.items():
        with Database(profile, request.timeout_seconds) as db:
            version = db.version()
            supported[side] = negotiate_format(db)
            before[side], material_issues = verify_materials(db, bundle.manifest)
            target_info[side] = {"version": version, "session": db.variables(), "materials": before[side],
                                 "endpoint": {"host": profile["host"], "port": profile.get("port", 4000)}}
            pre_issues.extend({**i, "side": side} for i in material_issues)
            target_info[side]["tiflash"], flash_issues = verify_tiflash(db, bundle.context.get("tiflash"))
            pre_issues.extend({**i, "side": side} for i in flash_issues)
            if request.binding_policy == "preserve":
                bindings = binding_evidence(db.records("SHOW GLOBAL BINDINGS"))
                target_info[side]["bindings"] = bindings
                if bindings != bundle.context.get("bindings"):
                    pre_issues.append({"code": "BINDINGS_MISMATCH", "side": side})
    common = [fmt for fmt in ("tidb_json", "row") if all(fmt in supported[s] for s in supported)]
    if not common:
        raise PCCError("PLAN_FORMAT_UNSUPPORTED", "两端没有共同支持的计划格式")
    fingerprint = digest(business_value({"bundle": _bundle_business(bundle), "targets": target_info,
        "session": request.session, "profile_sessions": {s: p.get("session", {}) for s, p in target_profiles.items()},
        "binding_policy": request.binding_policy, "versions": VERSIONS, "format": common[0],
        "timeout_seconds": request.timeout_seconds, "stats_retries": request.stats_retries}))
    events.emit("compare_started", fingerprint=fingerprint, format=common[0], targets=target_info)
    snapshots = {}
    with Publication(request.out, fingerprint, request.resume) as publication:
        root = publication.work
        capture_dir = root / "captures"
        capture_dir.mkdir(exist_ok=True)
        query_ids = {q.query_id for q in bundle.queries}
        if request.resume:
            for path in sorted(capture_dir.glob("*.json")):
                raw = read_json(path)
                if digest(raw) != path.name.rsplit(".", 2)[-2]:
                    raise PCCError("HASH_MISMATCH", "原始计划检查点被修改")
                snapshot = PlanSnapshot(**raw)
                if snapshot.query_id not in query_ids or snapshot.side not in {"old", "new"}:
                    raise PCCError("ARTIFACT_INVALID", "恢复计划不属于本次输入")
                if snapshot.capture_status == "ok":
                    snapshots[snapshot.query_id, snapshot.side] = snapshot
        for side in ("old", "new"):
            path = root / f"plans/{side}.jsonl"
            if request.resume and path.exists():
                for raw in read_jsonl(path):
                    snapshot = PlanSnapshot(**raw)
                    if snapshot.capture_status == "ok":
                        snapshots[(snapshot.query_id, side)] = snapshot
        semaphores = {side: threading.Semaphore(request.concurrency) for side in ("old", "new")}

        def capture(q, side):
            with semaphores[side]:
                return capture_plan(q, side, target_profiles[side], format=common[0], session=request.session,
                                    timeout_seconds=request.timeout_seconds, stats_retries=request.stats_retries,
                                    binding_policy=request.binding_policy, cancel=cancel)

        with ThreadPoolExecutor(max_workers=request.concurrency * 2) as pool:
            futures = {}
            # Bounded queue: do not enqueue the whole workload before checking cancellation.
            work = iter((q, s) for q in bundle.queries for s in ("old", "new") if (q.query_id, s) not in snapshots)
            exhausted = False
            while futures or not exhausted:
                while not exhausted and not cancel.is_set() and len(futures) < request.concurrency * 2:
                    try:
                        q, side = next(work)
                    except StopIteration:
                        exhausted = True
                        break
                    future = pool.submit(capture, q, side)
                    futures[future] = (q.query_id, side)
                if cancel.is_set():
                    exhausted = True
                if not futures:
                    break
                from concurrent.futures import FIRST_COMPLETED, wait
                done, _ = wait(futures, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    key = futures.pop(future)
                    try:
                        snapshot = future.result()
                    except Exception:
                        snapshot = PlanSnapshot(*key, capture_status="failed", issues=[{"code": "CAPTURE_INTERNAL_ERROR"}])
                    snapshots[key] = snapshot
                    events.emit("plan_captured", query_id=key[0], side=key[1], capture_status=snapshot.capture_status,
                                cancellation=snapshot.cancellation)
                    # Content-addressed snapshots survive abrupt process death without quadratic journal rewrites.
                    write_json(capture_dir / f"{key[0]}.{key[1]}.{digest(snapshot)}.json", snapshot)
        for side, profile in target_profiles.items():
            try:
                with Database(profile) as db:
                    after, material_issues = verify_materials(db, bundle.manifest)
                    pre_issues.extend({**i, "side": side} for i in material_issues)
                    if after != before[side] or db.version() != target_info[side]["version"]:
                        pre_issues.append({"code": "ENVIRONMENT_DRIFT", "side": side})
                    flash, flash_issues = verify_tiflash(db, bundle.context.get("tiflash"))
                    pre_issues.extend({**i, "side": side} for i in flash_issues)
                    if flash != target_info[side]["tiflash"]:
                        pre_issues.append({"code": "ENVIRONMENT_DRIFT", "side": side})
                    if request.binding_policy == "preserve" and binding_evidence(db.records("SHOW GLOBAL BINDINGS")) != target_info[side]["bindings"]:
                        pre_issues.append({"code": "BINDINGS_DRIFT", "side": side})
            except PCCError as exc:
                pre_issues.append({"code": "POSTCHECK_FAILED", "side": side, "cause": exc.code})
        result, irs = _summarize(bundle.queries, snapshots, fingerprint, pre_issues, cancel.is_set())
        result.output_dir = str(publication.work if cancel.is_set() else publication.output)
        events.emit("compare_completed", run_status=result.run_status, summary=result.summary, exit_code=result.exit_code)
        _write_result(root, result, bundle, snapshots, irs, events)
        publication.checkpoint(run_status=result.run_status, completed=[list(k) for k in sorted(snapshots)])
        manifest = {"kind": "ComparisonResult", "versions": VERSIONS, "run_status": result.run_status,
                    "business_fingerprint": digest(business_value({"items": result.items, "diffs": result.diffs})),
                    "targets": target_info, "created_at": now()}
        if not cancel.is_set():
            publication.publish(manifest)
        else:
            from pcc_tool.artifacts.store import finalize
            finalize(root, {**manifest, "fingerprint": fingerprint})
    return result


def diff(request: DiffRequest, *, on_event=None, cancel=None) -> ComparisonResult:
    root = Path(request.result_dir)
    manifest = verify(root, "ComparisonResult")
    queries = [QueryCase(**r) for r in read_jsonl(root / "inputs/queries.jsonl")]
    bundle = CaptureBundle(read_json(root / "inputs/manifest.json"), queries, read_json(root / "inputs/context.json"),
                           read_json(root / "inputs/selection.json"), read_jsonl(root / "inputs/issues.jsonl"))
    snapshots = {}
    query_ids = {q.query_id for q in queries}
    for side in ("old", "new"):
        for raw in read_jsonl(root / f"plans/{side}.jsonl"):
            snapshot = PlanSnapshot(**raw)
            key = (snapshot.query_id, side)
            if snapshot.side != side or key in snapshots or snapshot.query_id not in query_ids:
                raise PCCError("ARTIFACT_INVALID", "归档计划身份不匹配或重复")
            snapshots[key] = snapshot
    fingerprint = digest(business_value({"inputs": _bundle_business(bundle), "snapshots": [snapshots[k] for k in sorted(snapshots)],
                                         "versions": VERSIONS}))
    events = Events(on_event)
    events.emit("diff_started", fingerprint=fingerprint)
    # Keep capture/preflight failures, but recalculate parser/matcher findings with current algorithms.
    issues = [i for i in read_jsonl(root / "issues.jsonl") if i.get("code") not in
              {"PLAN_PARSE_FAILED", "PLAN_SEMANTICS_UNKNOWN", "PLAN_MATCH_AMBIGUOUS", "CONTEXT_DIFFERENCE"}]
    result, irs = _summarize(queries, snapshots, fingerprint, issues, bool(cancel and cancel.is_set()))
    with Publication(request.out, fingerprint, request.resume) as publication:
        result.output_dir = str(publication.output)
        events.emit("diff_completed", run_status=result.run_status, summary=result.summary, exit_code=result.exit_code)
        _write_result(publication.work, result, bundle, snapshots, irs, events)
        publication.checkpoint(run_status=result.run_status)
        publication.publish({"kind": "ComparisonResult", "versions": VERSIONS, "run_status": result.run_status,
                             "business_fingerprint": digest(business_value({"items": result.items, "diffs": result.diffs})),
                             "targets": manifest.get("targets", {}), "created_at": now()})
    return result


def doctor(request: DoctorRequest, *, profiles=None, on_event=None) -> dict:
    import importlib.metadata
    report = {"runtime": {"python": platform.python_version(), "system": platform.system(), "machine": platform.machine(),
                          "frozen": bool(getattr(sys, "frozen", False))}, "dependencies": {}, "targets": {}, "issues": []}
    if sys.version_info[:2] != (3, 11):
        report["issues"].append({"code": "RUNTIME_UNSUPPORTED", "message": "需要 CPython 3.11"})
    try:
        import importlib.resources
        import json
        schema = json.loads(importlib.resources.files("pcc_tool").joinpath("schemas/contracts-v1.schema.json").read_text("utf-8"))
        report["contract_schema"] = schema["$id"]
    except (OSError, ValueError, KeyError):
        report["issues"].append({"code": "RUNTIME_RESOURCE_MISSING", "message": "无法读取版本化 JSON 契约"})
    for name in ("pcc-tool", "PyMySQL", "sqlglot"):
        try:
            report["dependencies"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["dependencies"][name] = "missing_metadata"
    bundle = load_bundle(request.bundle) if request.bundle else None
    if bundle:
        report["bundle_hash"] = digest(bundle.manifest)
        report["issues"].extend(bundle.issues)
    for target in request.targets:
        try:
            profile = profile_named(profiles or {}, target)
            with Database(profile) as db:
                info = {"version": db.version(), "variables": db.variables(), "formats": negotiate_format(db),
                        "connection_id": db.scalar("SELECT CONNECTION_ID()")}
                try:
                    info["cluster"] = db.cluster_info()
                    info["summary_columns"] = db.query("SELECT * FROM information_schema.CLUSTER_STATEMENTS_SUMMARY LIMIT 0")[0]
                except PCCError as exc:
                    report["issues"].append({"code": "VISIBILITY_LIMITED", "target": target, "cause": exc.code})
                if bundle:
                    info["materials"], issues = verify_materials(db, bundle.manifest)
                    report["issues"].extend({**i, "target": target} for i in issues)
                report["targets"][target] = info
        except PCCError as exc:
            report["issues"].append({"code": exc.code, "target": target, "message": exc.message})
    report["run_status"] = "partial" if report["issues"] else "completed"
    if on_event:
        Events(on_event).emit("doctor_completed", report=report)
    return report
