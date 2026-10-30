"""Versioned, framework-independent SDK contracts."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "1"
VERSIONS = dict(contract_version="1", ir_version="1", normalizer_version="2",
                matcher_version="3", diff_version="2", guard_version="3", materials_version="1", rules_version="1")
METRICS = ("execution_count", "total_latency", "average_latency", "total_keys", "average_keys")


class PCCError(Exception):
    """Safe, public error. Do not construct this with raw driver exceptions."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return primitive(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, (Path, datetime)):
        return str(value)
    if isinstance(value, float):
        raise TypeError("Binary floating point is not permitted in PCC contracts")
    return value


def canonical(value: Any) -> str:
    text = json.dumps(primitive(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def byte_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def business_value(value: Any) -> Any:
    """Remove run metadata, not SQL literals or observation-window boundaries."""
    value = primitive(value)
    transient = {"run_id", "captured_at", "created_at", "collected_at", "time", "output_dir", "path",
                 "materials_path", "elapsed_seconds", "connection_id", "ddl_captured_at", "stats_captured_at"}
    if isinstance(value, dict):
        return {k: business_value(v) for k, v in value.items() if k not in transient}
    if isinstance(value, list):
        return [business_value(v) for v in value]
    return value


@dataclass
class Issue:
    code: str
    message: str
    query_id: str | None = None
    side: str | None = None
    severity: str = "error"
    evidence: dict = field(default_factory=dict)


@dataclass
class QueryCase:
    query_id: str
    schema: str
    sql_text: str | None
    sql_hash: str | None
    source_digest: str = ""
    source_cluster: str = ""
    statement_type: str = "select"
    executable_status: str = "ok"
    sample_origins: list[dict] = field(default_factory=list)
    source_plan_refs: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    dependencies: list[list[str]] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    parameters: list[dict] | None = None

    @classmethod
    def create(cls, sql_text: str | None, schema: str = "", context: dict | None = None,
               origin: dict | None = None, **kwargs) -> QueryCase:
        context, origin = context or {}, origin or {}
        sql_hash = byte_hash(sql_text.encode("utf-8", "surrogateescape")) if sql_text else None
        identity = {"sql_hash": sql_hash, "schema": schema, "context": context}
        if kwargs.get("parameters") is not None:
            identity["parameters"] = kwargs["parameters"]
        if not sql_hash:
            stable_keys = ("source_cluster", "source_epoch", "instance", "summary_begin_time", "schema_name",
                           "sql_digest", "plan_digest", "previous_statement_key")
            identity["unresolved"] = ({k: origin.get(k) for k in stable_keys} if origin.get("sql_digest")
                                      else business_value(origin))
        qid = ("q_" if sql_hash else "unresolved_") + digest(identity)
        return cls(qid, schema, sql_text or None, sql_hash, context=context,
                   sample_origins=[origin] if origin else [], **kwargs)


@dataclass
class CaptureBundle:
    manifest: dict
    queries: list[QueryCase]
    context: dict = field(default_factory=dict)
    selection: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    path: str = ""


@dataclass
class PlanSnapshot:
    query_id: str
    side: str
    version: str = "unknown"
    context: dict = field(default_factory=dict)
    format: str = "row"
    columns: list[str] = field(default_factory=list)
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    captured_at: str = field(default_factory=now)
    capture_status: str = "ok"
    issues: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    cancellation: str | None = None


@dataclass
class PlanNode:
    node_id: str
    operator: str
    task: str = ""
    engine: str = ""
    access: str = ""
    attributes: str = ""
    estimate: str | None = None
    role: str = ""
    children: list[PlanNode] = field(default_factory=list)
    unknown: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


@dataclass
class PlanIR:
    query_id: str
    roots: list[PlanNode]
    parse_status: str = "ok"
    issues: list[dict] = field(default_factory=list)
    versions: dict = field(default_factory=lambda: dict(VERSIONS))


@dataclass
class DiffEvent:
    query_id: str
    type: str
    old_node: str | None
    new_node: str | None
    field: str
    old: Any
    new: Any
    evidence: dict = field(default_factory=dict)
    confidence: str = "high"
    requires_review: bool = False
    compare_status: str = "unassessed"


@dataclass
class ComparisonResult:
    fingerprint: str
    summary: dict
    items: list[dict]
    diffs: list[DiffEvent]
    issues: list[dict]
    run_status: str
    output_dir: str = ""

    @property
    def exit_code(self) -> int:
        if self.run_status == "interrupted":
            return 4
        if self.run_status == "blocked":
            return 2
        if self.run_status == "partial":
            return 3
        return 1 if self.diffs else 0


@dataclass
class CompareRequest:
    bundle: str
    old: str
    new: str
    out: str
    resume: bool = False
    concurrency: int = 2
    timeout_seconds: int = 30
    stats_retries: int = 1
    binding_policy: str = "exclude"
    session: dict = field(default_factory=dict)


@dataclass
class DiffRequest:
    result_dir: str
    out: str
    resume: bool = False


@dataclass
class ExportRequest:
    out: str
    source: str | None = None
    from_summary: bool = False
    spool: str | None = None
    repository: str | None = None
    legacy_package: str | None = None
    sql_file: str | None = None
    dependencies_bundle: str | None = None
    begin: str | None = None
    end: str | None = None
    top_n: int = 20
    order_by: tuple[str, ...] = METRICS
    ranking_scope: str = "global"
    schemas: tuple[str, ...] = ()
    statement_types: tuple[str, ...] = ("select", "update", "delete")


@dataclass
class CollectRequest:
    source: str
    out: str
    watch: bool = False
    interval_seconds: int = 300


@dataclass
class PrepareRequest:
    bundle: str
    targets: tuple[str, ...]
    out: str


@dataclass
class DoctorRequest:
    targets: tuple[str, ...] = ()
    bundle: str | None = None
