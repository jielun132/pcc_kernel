import threading

import pytest

from pcc_tool.capture.connection import validate_profile
from pcc_tool.capture.explain import capture_plan
from pcc_tool.models import PCCError, QueryCase


class FakeDB:
    calls = []
    pseudo = False

    def __init__(self, profile, timeout=30):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def version(self):
        return "v6.5.12"

    def set_session(self, settings):
        self.calls.append(("session", settings))

    def variables(self):
        return {"sql_mode": ""}

    def scalar(self, sql):
        self.calls.append((sql, ()))
        return 123

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        if sql.startswith("EXPLAIN"):
            return ["id", "estRows", "task", "access object", "operator info"], [
                ["TableDual_1", "1", "root", "", "stats:pseudo" if self.pseudo else "rows:1"]]
        return [], []

    def records(self, sql, params=()):
        self.calls.append((sql, params))
        return []


def test_original_sql_and_warning_order():
    FakeDB.calls = []
    text = "SELECT /*+ HASH_JOIN(a,b) */ * FROM a JOIN b ON a.id=b.id"
    q = QueryCase.create(text, "app")
    snap = capture_plan(q, "old", {"host": "fake"}, db_factory=FakeDB)
    calls = [x[0] for x in FakeDB.calls]
    idx = next(i for i, sql in enumerate(calls) if sql.startswith("EXPLAIN"))
    assert calls[idx] == "EXPLAIN FORMAT='row' " + text
    assert calls[idx+1] == "SHOW WARNINGS"
    assert not any(sql.startswith(("ANALYZE", "SELECT /*+", "UPDATE", "DELETE")) for sql in calls)
    assert snap.capture_status == "ok"


def test_pseudo_is_incomplete_with_attempt_evidence():
    FakeDB.calls, FakeDB.pseudo = [], True
    try:
        snap = capture_plan(QueryCase.create("SELECT 1"), "old", {"host": "fake"}, stats_retries=0, db_factory=FakeDB)
        assert snap.capture_status == "ok"
        assert len(snap.attempts) == 1
        assert any(i["code"] == "STATS_NOT_READY" for i in snap.issues)
    finally:
        FakeDB.pseudo = False


def test_pre_cancel_does_not_connect():
    cancel = threading.Event()
    cancel.set()
    def fail(*a, **k):
        raise AssertionError("connected after cancel")
    snap = capture_plan(QueryCase.create("SELECT 1"), "old", {}, cancel=cancel, db_factory=fail)
    assert snap.capture_status == "skipped"


@pytest.mark.parametrize("extra", [{"password": "example"}, {"token": "example"},
                                  {"status_url": "https://user:password@example.invalid"}, {"local_infile": True}])
def test_no_inline_credentials(extra):
    with pytest.raises(PCCError):
        validate_profile({"host": "example.invalid", "user": "reader", **extra})


def test_in_flight_cancel_checks_server_completion():
    cancel, running, stopped = threading.Event(), threading.Event(), threading.Event()
    calls = []
    class SlowDB(FakeDB):
        def query(self, sql, params=()):
            calls.append(sql)
            if sql.startswith("EXPLAIN"):
                running.set()
                assert stopped.wait(3), "cancel did not kill task query"
                raise PCCError("DB_QUERY_FAILED", "query interrupted")
            if sql.startswith("KILL TIDB QUERY"):
                stopped.set()
            return [], []
        def records(self, sql, params=()):
            calls.append(sql)
            return []
    result = []
    worker = threading.Thread(target=lambda: result.append(capture_plan(QueryCase.create("SELECT 1"), "old", {"host": "fake"},
                        cancel=cancel, db_factory=SlowDB)))
    worker.start()
    assert running.wait(1)
    cancel.set()
    worker.join(4)
    assert not worker.is_alive() and result[0].cancellation == "confirmed"
    assert any(sql.startswith("SELECT ID, COMMAND FROM information_schema.CLUSTER_PROCESSLIST") for sql in calls)
    assert result[0].capture_status == "failed"
    assert {i["code"] for i in result[0].issues} >= {"CANCELLED", "DB_QUERY_FAILED"}


def test_raw_percent_not_driver_interpolation():
    from pcc_tool.capture.connection import Database
    seen = []
    class Cursor:
        description = []
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def execute(self, sql, params):
            seen.append((sql, params))
    class Conn:
        def cursor(self):
            return Cursor()
    db = Database({"host": "fake", "user": "reader"})
    db.connection = Conn()
    db.query("EXPLAIN SELECT * FROM t WHERE x LIKE '%literal%' OR x='%s'")
    assert seen[0][1] is None


def test_effective_lexical_sql_mode_rejected():
    class UnsupportedModeDB(FakeDB):
        def variables(self):
            return {"sql_mode": "ANSI_QUOTES"}
    snap = capture_plan(QueryCase.create("SELECT 1"), "old", {"host": "fake"}, db_factory=UnsupportedModeDB)
    assert snap.capture_status == "failed"
    assert snap.issues[0]["code"] == "SQL_MODE_UNSUPPORTED"


def test_driver_secret_is_not_exposed(monkeypatch):
    import pymysql
    from pcc_tool.capture.connection import Database
    sentinel = "unit-test-secret-must-not-appear"
    monkeypatch.setenv("PCC_FAKE_SECRET", sentinel)
    def fail(**kwargs):
        assert kwargs["password"] == sentinel and not kwargs["local_infile"]
        assert not kwargs["client_flag"] & pymysql.constants.CLIENT.MULTI_STATEMENTS
        raise pymysql.err.OperationalError(1045, sentinel)
    monkeypatch.setattr(pymysql, "connect", fail)
    with pytest.raises(PCCError) as err:
        with Database({"host": "fake", "user": "reader", "password_env": "PCC_FAKE_SECRET"}):
            pass
    assert sentinel not in str(err.value) and "1045" in str(err.value)


def test_configured_tls_must_not_fall_back(monkeypatch):
    import pymysql
    from pcc_tool.capture.connection import Database
    class Plain:
        _sock = object()
        closed = False
        def close(self):
            self.closed = True
    plain = Plain()
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: plain)
    with pytest.raises(PCCError) as err:
        with Database({"host": "fake", "user": "reader", "tls": {}}):
            pass
    assert err.value.code == "TLS_REQUIRED" and plain.closed
