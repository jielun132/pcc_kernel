import json

import pytest

from pcc_tool.collector import dependencies as module
from pcc_tool.collector.source import read_summary
from pcc_tool.collector.summary import make_query
from pcc_tool.models import PCCError


class SourceDB:
    profile = {"host": "source", "source_cluster": "test", "timezone": "Asia/Shanghai"}
    calls = []
    table_kind = "BASE TABLE"

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SHOW CREATE DATABASE"):
            return ["Database", "Create Database"], [["app", "CREATE DATABASE `app`"]]
        if sql.startswith("SHOW CREATE TABLE"):
            name = "v" if "`v`" in sql else "t"
            ddl = "CREATE VIEW `v` AS SELECT * FROM app.t" if name == "v" else "CREATE TABLE `t` (`view` int, id int primary key)"
            return ["Table", "Create Table"], [[name, ddl]]
        if sql.startswith("SELECT * FROM information_schema.CLUSTER_STATEMENTS"):
            return ["SUMMARY_BEGIN_TIME", "SUMMARY_END_TIME", "STMT_TYPE", "SCHEMA_NAME", "DIGEST", "INSTANCE",
                    "QUERY_SAMPLE_TEXT", "EXEC_COUNT", "SUM_LATENCY", "AVG_TOTAL_KEYS", "PREV_SAMPLE_TEXT"], []
        raise AssertionError(sql)

    def scalar(self, sql, params=()):
        if sql.startswith("SELECT TABLE_TYPE"):
            return "VIEW" if params[1] == "v" else "BASE TABLE"
        raise AssertionError(sql)

    def records(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("SHOW STATS_META"):
            return [{"Db_name": "app", "Table_name": "t", "Partition_name": "", "Row_count": "1", "Modify_count": "0", "Update_time": "time"}]
        if sql.startswith("SHOW STATS_HISTOGRAMS"):
            return [{"Db_name": "app", "Table_name": "t", "Partition_name": "", "Column_name": "id", "Is_index": "0", "Distinct_count": "1", "Null_count": "0"}]
        if sql.startswith("SELECT `SUMMARY_BEGIN_TIME`"):
            return [{"SUMMARY_BEGIN_TIME": "2026-10-08T09:00:00", "SUMMARY_END_TIME": "2026-10-08T09:30:00", "STMT_TYPE": "Select",
                     "SCHEMA_NAME": "app", "DIGEST": "d", "INSTANCE": "a", "QUERY_SAMPLE_TEXT": "SELECT * FROM t WHERE id=1",
                     "EXEC_COUNT": "10", "SUM_LATENCY": "100", "AVG_TOTAL_KEYS": "2", "PREV_SAMPLE_TEXT": ""}]
        if sql.startswith(("SHOW GLOBAL BINDINGS", "SELECT * FROM information_schema.TIFLASH_REPLICA", "SHOW WARNINGS",
                           "SELECT * FROM information_schema.CLUSTER_STATEMENTS_SUMMARY_EVICTED")):
            return []
        raise AssertionError(sql)

    def cluster_info(self):
        return [{"TYPE": "tidb", "INSTANCE": "a", "START_TIME": "epoch"}]

    def variables(self, names=None):
        return {"tidb_enable_stmt_summary": "1", "tidb_stmt_summary_refresh_interval": "1800", "tidb_stmt_summary_max_sql_length": "4096"}

    def version(self):
        return "v6.5.12"


def test_source_reads_whole_requested_range():
    db = SourceDB()
    db.calls = []
    rows, metadata = read_summary(db, "prod", "2026-10-08T00:00:00+08:00", "2026-10-09T00:00:00+08:00")
    selects = [(sql, args) for sql, args in db.calls if sql.startswith("SELECT `SUMMARY_BEGIN_TIME`")]
    assert len(selects) == 2
    assert all("SUMMARY_BEGIN_TIME < %s AND SUMMARY_END_TIME > %s" in sql for sql, _ in selects)
    assert all(params[0].hour == 0 and params[1].hour == 0 for _, params in selects)
    assert all(r["summary_begin_time"].endswith("+08:00") for r in rows)
    assert all(r["source_epoch"] == "epoch" and r["key_quality"] == "lossy" for r in rows)
    assert metadata["historical_session"] == "unknown"


def test_truncated_multibyte_summary_roundtrips_without_blocking_collection(tmp_path):
    from pcc_tool.artifacts.store import read_jsonl, write_jsonl
    from pcc_tool.models import byte_hash
    raw = b"SELECT '" + "中文😀".encode("utf-8")[:-2]
    sample = raw.decode("utf-8", "surrogateescape")
    class TruncatedDB(SourceDB):
        def records(self, sql, params=()):
            rows = super().records(sql, params)
            if sql.startswith("SELECT `SUMMARY_BEGIN_TIME`"):
                rows[0]["QUERY_SAMPLE_TEXT"] = sample
            return rows
    rows, _ = read_summary(TruncatedDB(), "prod", "2026-10-08T00:00:00+08:00", "2026-10-09T00:00:00+08:00")
    assert all(r["truncated"] for r in rows)
    query = make_query(rows[0]["query_sample_text"], "app", truncated=True)
    assert query.executable_status == "needs_sample" and query.sql_hash == byte_hash(raw)
    assert make_query(sample, "app").executable_status == "needs_sample"
    write_jsonl(tmp_path / "queries.jsonl", [query])
    assert read_jsonl(tmp_path / "queries.jsonl")[0]["sql_text"].encode("utf-8", "surrogateescape") == raw


def test_view_dependency_order_and_raw_stats(tmp_path, monkeypatch):
    raw = b'{"database_name":"app", "table_name":"t", "count":9007199254740993, "columns":{}, "indices":{}}'
    monkeypatch.setattr(module, "verify_status_identity", lambda *a: {"verified": True})
    monkeypatch.setattr(module, "status_json", lambda *a, **k: raw if k.get("raw") else json.loads(raw))
    db = SourceDB()
    db.calls = []
    query = make_query("SELECT * FROM app.v")
    databases, objects, context, issues = module.export_dependencies(db, [query], tmp_path)
    assert [o["table"] for o in objects] == ["t", "v"]
    assert not issues
    table = objects[0]
    assert (tmp_path / table["stats_file"]).read_bytes() == raw
    assert json.loads((tmp_path / table["stats_file"]).read_bytes())["count"] == 9007199254740993
    assert len(databases) == 1 and context["stats_time_semantics"] == "export_time_not_business_window"
    state, issues = module.verify_materials(db, {"objects": objects})
    assert not issues


def test_unknown_status_identity_never_claims_ready(tmp_path, monkeypatch):
    def failed(*args):
        raise PCCError("STATUS_IDENTITY_UNVERIFIED", "not verified")
    monkeypatch.setattr(module, "verify_status_identity", failed)
    _, objects, _, issues = module.export_dependencies(SourceDB(), [make_query("SELECT * FROM app.t")], tmp_path)
    assert objects[0]["stats_file"] is None
    assert any(i["code"] == "STATUS_IDENTITY_UNVERIFIED" for i in issues)


def test_persisted_stats_verify_independent_of_lazy_cache(monkeypatch):
    import copy
    stats = {"database_name": "app", "table_name": "t", "count": 10,
             "columns": {"id": {"histogram": {"ndv": 10, "buckets": [1, 10]}}}}
    db = SourceDB()
    db.profile = {**db.profile, "status_url": "http://source:10080"}
    obj = {"schema": "app", "table": "t", "kind": "table", "stats_ready": True,
           "ddl_hash": module.ddl_signature(module.show_create(db, "app", "t")),
           "stats_fingerprint": module.stats_fingerprint(stats)}
    monkeypatch.setattr(module, "verify_status_identity", lambda *a: {"verified": True})
    monkeypatch.setattr(module, "status_json", lambda *a: stats)
    monkeypatch.setattr(module, "stats_evidence", lambda *a: pytest.fail("must not depend on the partial node cache"))
    state, issues = module.verify_materials(db, {"objects": [obj]})
    assert not issues and state
    changed = copy.deepcopy(stats)
    changed["columns"]["id"]["histogram"]["buckets"][0] = 2
    monkeypatch.setattr(module, "status_json", lambda *a: changed)
    _, issues = module.verify_materials(db, {"objects": [obj]})
    assert issues[0]["code"] == "STATS_NOT_READY"


def test_bindings_ignore_only_capture_times():
    first = [{"Original_sql": "select * from t", "Bind_sql": "select /*+ use_index(t,idx) */ * from t", "Create_time": "one", "Update_time": "one"}]
    second = [{**first[0], "Create_time": "two", "Update_time": "two"}]
    assert module.binding_evidence(first) == module.binding_evidence(second)
    second[0]["Bind_sql"] = "select /*+ use_index(t,other) */ * from t"
    assert module.binding_evidence(first) != module.binding_evidence(second)


def test_tiflash_missing_is_not_ready():
    _, issues = module.verify_tiflash(SourceDB(), [{"TABLE_SCHEMA": "app", "TABLE_NAME": "t", "REPLICA_COUNT": "1"}])
    assert issues[0]["code"] == "TIFLASH_NOT_READY"


@pytest.mark.parametrize("status_version", ["v6.5.12", "5.7.25-TiDB-v6.5.12"])
def test_status_identity_handles_compatibility_prefix(monkeypatch, status_version):
    from pcc_tool.capture import connection
    class DB:
        def cluster_info(self):
            return [{"TYPE": "tidb", "STATUS_ADDRESS": "source:10080", "VERSION": "v6.5.12", "GIT_HASH": "commit"}]
    monkeypatch.setattr(connection, "status_json", lambda *a: {"version": status_version, "git_hash": "commit"})
    assert connection.verify_status_identity(DB(), {"status_url": "http://source:10080"})["verified"]
    monkeypatch.setattr(connection, "status_json", lambda *a: {"version": status_version, "git_hash": "other"})
    with pytest.raises(PCCError):
        connection.verify_status_identity(DB(), {"status_url": "http://source:10080"})
