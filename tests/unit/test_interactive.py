import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from pcc_tool import api, cli, demo, interactive, workflow
from pcc_tool.artifacts.store import finalize, load_bundle, read_json, verify, write_bytes, write_json, write_jsonl
from pcc_tool.models import ExportRequest, PCCError, PrepareRequest, VERSIONS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "pcc.json"
    value = json.loads((ROOT.parent / "examples/pcc.example.json").read_text())
    for name, profile in value["profiles"].items():
        profile["host"] = name + ".invalid"
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def material_bundle(tmp_path):
    root = tmp_path / "bundle"
    write_bytes(root / "metadata_backup/db.sql", b"CREATE DATABASE `demo`;")
    write_bytes(root / "metadata_backup/t.sql", b"CREATE TABLE `t` (id INT PRIMARY KEY);")
    write_jsonl(root / "queries.jsonl", [])
    write_json(root / "selection.json", {})
    write_json(root / "context.json", {})
    write_jsonl(root / "issues.jsonl", [])
    finalize(root, {"kind": "CaptureBundle", "versions": VERSIONS,
                    "databases": [{"schema": "demo", "ddl_file": "metadata_backup/db.sql"}],
                    "objects": [{"schema": "demo", "table": "t", "kind": "table", "ddl_file": "metadata_backup/t.sql"}]})
    return load_bundle(str(root))


def test_packaged_corpus_is_byte_for_byte_original():
    resources = ROOT.parent / "src/pcc_tool/demo_data"
    for source in (ROOT / "cases/sql_cases.jsonl", ROOT / "cases/catalog.lock.json",
                   ROOT / "fixtures/live_setup.sql", ROOT / "fixtures/sql_matrix_setup.sql"):
        assert (resources / source.name).read_bytes() == source.read_bytes()
    assert (resources / "pcc.example.json").read_bytes() == (ROOT.parent / "examples/pcc.example.json").read_bytes()


def test_demo_preserves_all_cases_and_parameters_without_database(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "Database", lambda *a, **k: pytest.fail("Offline generation connected"))
    out = tmp_path / "demo"
    result = demo.generate(str(out), "pcc_customer_01")
    verify(out, "DemoCases")
    assert result["case_count"] == 413 and len(result["categories"]) == 17
    assert result["expected_guards"] == {"ok": 402, "needs_sample": 6, "unsupported": 5}
    samples = [json.loads(s) for s in (out / "queries.jsonl").read_text().splitlines()]
    for original, sample in zip(demo.catalog(), samples):
        assert sample["parameters"] == original["parameters"]
        assert sample["origin"]["case_id"] == original["id"]
        sql = original["sql_text"]
        for name in demo.SCHEMAS:
            sql = sql.replace("pcc_case_" + name, "pcc_customer_01_" + name)
        assert sample["sql_text"] == sql
    bundle = api.export(ExportRequest(str(tmp_path / "export"), sql_file=str(out / "queries.jsonl"),
                                     statement_types=("select", "update", "delete", "insert", "replace")))
    assert len(bundle.queries) == 413
    assert [q.sample_origins[0]["case_id"] for q in bundle.queries] == [c["id"] for c in demo.catalog()]
    assert [q.executable_status for q in bundle.queries] == [c["expected_guard"] for c in demo.catalog()]


def test_demo_setup_is_only_new_schema_fixtures(tmp_path):
    demo.generate(str(tmp_path / "demo"), "pcc_fixture")
    from sqlglot.tokens import TokenType
    sql = (tmp_path / "demo/setup.sql").read_text()
    statements, start = [], 0
    for token in sqlglot.tokenize(sql, read="mysql"):
        if token.token_type == TokenType.SEMICOLON:
            statements.append(sql[start:token.end + 1])
            start = token.end + 1
    for statement in statements:
        first = sqlglot.tokenize(statement, read="mysql")[0].text.upper()
        assert first in {"CREATE", "INSERT", "ANALYZE", "SET", "SELECT", "USE"}
        if first == "USE":
            assert sqlglot.parse_one(statement, read="mysql").this.name in {"pcc_fixture_" + n for n in demo.SCHEMAS}
    # SQLGlot 28 cannot parse TiDB's RANGE partition definition. The live import
    # validates that DDL; parse every other statement here, including every INSERT.
    trees = [sqlglot.parse_one(s, read="mysql") for s in statements if "PARTITION BY RANGE" not in s]
    assert len([x for x in trees if isinstance(x, exp.Create) and x.args["kind"] == "DATABASE"]) == 3
    inserts = [x for x in trees if isinstance(x, exp.Insert)]
    assert sum(len(x.expression.expressions) for x in inserts) == (100 + 1000 + 2000 + 240 + 32) * 3
    for tree in inserts:
        table = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
        assert table.db in {"pcc_fixture_" + n for n in demo.SCHEMAS}
    with pytest.raises(PCCError):
        demo.generate(str(tmp_path / "demo"), "pcc_fixture")


@pytest.mark.parametrize("prefix", ["business", "pcc_foo`;DROP DATABASE mysql", "pcc_" + "a" * 41, "pcc_A"])
def test_demo_rejects_unsafe_prefix(tmp_path, prefix):
    with pytest.raises(PCCError):
        demo.generate(str(tmp_path / "demo"), prefix)
    assert not (tmp_path / "demo").exists()


def test_cli_no_terminal_never_prompts(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("Unexpected prompt"))
    assert cli.main([]) == 2
    assert cli.main(["ui"]) == 2
    assert "终端" in capsys.readouterr().err


def test_first_start_generates_config_without_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert interactive.run() == 0
    assert (tmp_path / "pcc.json").read_bytes() == (ROOT.parent / "examples/pcc.example.json").read_bytes()
    assert interactive.settings(tmp_path / "pcc.json")["concurrency"] == api.CompareRequest("b", "old", "new", "out").concurrency == 8
    assert cli.parser().parse_args(["compare", "--bundle","b","--old","old","--new","new","--out","out"]).concurrency == 8
    assert not (tmp_path / "pcc-output").exists()


def test_menu_generate_persists_and_does_not_request_password(config, monkeypatch):
    answers = iter(["1", "0"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(interactive.getpass, "getpass", lambda *a: pytest.fail("Generation requested password"))
    assert interactive.run(str(config)) == 0
    state = read_json(config.parent / "pcc-output/ui-state.json")
    assert verify(Path(state["demo"]), "DemoCases")["case_count"] == "413"


@pytest.mark.parametrize("patch", [{"stats_retries": 6}, {"concurrency": True}, {"out_dir": ""}, {"top_n": 0},
                                  {"unknown": 1}, {"schemas": "app"}, {"order_by": ["fake"]}])
def test_config_validation(config, patch):
    value = json.loads(config.read_text())
    value["workflow"].update(patch)
    config.write_text(json.dumps(value))
    with pytest.raises(PCCError):
        interactive.settings(config)


def test_config_paths_and_local_time(config):
    cfg = interactive.settings(config)
    assert Path(cfg["out_dir"]) == config.parent / "pcc-output"
    assert interactive.time_value("2026-11-03 09:30:00", "Asia/Shanghai") == "2026-11-03T09:30:00+08:00"


def test_password_reused_and_removed_not_written(config, monkeypatch):
    monkeypatch.delenv("PCC_DB_PASSWORD", raising=False)
    calls = []
    monkeypatch.setattr(interactive.getpass, "getpass", lambda p: calls.append(p) or "test-only-secret")
    menu = interactive.Menu(config)
    menu.password(("prod", "old", "new"))
    assert len(calls) == 1 and os.environ["PCC_DB_PASSWORD"] == "test-only-secret"
    menu.save()
    assert "test-only-secret" not in config.read_text() + menu.index_path.read_text()
    menu.close()
    assert "PCC_DB_PASSWORD" not in os.environ


def fake_databases(monkeypatch, *, existing=(), shared=False):
    reads = []
    class Fake:
        def __init__(self, profile):
            self.profile = profile
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def version(self):
            return self.profile["expected_version"]
        def cluster_info(self):
            return [{"TYPE": "tidb", "INSTANCE": "shared" if shared else self.profile["host"]}]
        def scalar(self, sql, params):
            reads.append((self.profile["host"], params))
            return "1" if self.profile["host"] in existing else "0"
    monkeypatch.setattr(workflow, "Database", Fake)
    return reads


def test_both_targets_preflight_before_import(config, monkeypatch):
    profiles = interactive.load_profiles(str(config))
    reads = fake_databases(monkeypatch)
    versions = workflow.inspect_targets(profiles, "prod", "old", "new", ["demo"], ["old", "new"])
    assert set(versions) == {"prod", "old", "new"}
    assert reads == [("old.invalid", ("demo",)), ("new.invalid", ("demo",))]


@pytest.mark.parametrize("kind", ["existing", "shared", "role", "version", "endpoint"])
def test_preflight_rejects_nonempty_or_unisolated(config, monkeypatch, kind):
    profiles = interactive.load_profiles(str(config))
    fake_databases(monkeypatch, existing=["new.invalid"] if kind == "existing" else [], shared=kind == "shared")
    if kind == "role":
        profiles["old"]["role"] = "source"
    if kind == "version":
        profiles["old"].pop("expected_version")
    if kind == "endpoint":
        profiles["old"]["host"] = profiles["prod"]["host"]
    with pytest.raises(PCCError):
        workflow.inspect_targets(profiles, "prod", "old", "new", ["demo"], ["old", "new"])


def test_import_validates_bundle_and_passes_password_only_in_environment(config, material_bundle, monkeypatch, tmp_path):
    profiles = interactive.load_profiles(str(config))
    prepared = tmp_path / "prepared"
    api.prepare(PrepareRequest(material_bundle.path, ("old", "new"), str(prepared)), profiles=profiles)
    fake_databases(monkeypatch)
    monkeypatch.setattr(workflow.shutil, "which", lambda p: "/bin/mysql")
    monkeypatch.setenv("PCC_DB_PASSWORD", "test-only-secret")
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(workflow.subprocess, "run", run)
    workflow.import_target(str(prepared), material_bundle.path, "old", profiles["old"], "mysql")
    args, options = calls[0]
    assert all("test-only-secret" not in a for a in args)
    assert options["env"]["MYSQL_PWD"] == "test-only-secret"
    assert b"CREATE DATABASE" in options["input"] and "--force" not in args
    assert options.get("shell", False) is False
    changed = dict(profiles["old"], host="another.invalid")
    with pytest.raises(PCCError, match="不一致"):
        workflow.import_target(str(prepared), material_bundle.path, "old", changed, "mysql")
    assert len(calls) == 1


@pytest.mark.parametrize("sql", ["DROP DATABASE demo;", "CREATE DATABASE demo; DROP DATABASE mysql;",
                               "CREATE DATABASE mysql;", "CREATE DATABASE IF NOT EXISTS demo;"])
def test_import_never_executes_unsafe_ddl(material_bundle, sql):
    write_bytes(Path(material_bundle.path) / "metadata_backup/db.sql", sql.encode())
    with pytest.raises(PCCError):
        workflow.validate_materials(material_bundle)


def test_cli_demo_events_and_offline_config_ignored(tmp_path, capsys):
    assert cli.main(["--config", "/does/not/exist", "--events", "jsonl", "demo", "--out", str(tmp_path / "demo")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["case_count"] == "413"


def test_fixed_bundle_reuses_verified_inputs_without_export(config, material_bundle, monkeypatch, tmp_path):
    import shutil
    menu = interactive.Menu(config)
    source = Path(material_bundle.path)
    invalid = tmp_path / "unlisted-file"
    shutil.copytree(source, invalid)
    (invalid / "unlisted.txt").write_text("outside the reference")
    monkeypatch.setattr("builtins.input", lambda *a: str(invalid))
    with pytest.raises(PCCError) as exc:
        menu.start(False)
    assert exc.value.code == "ARTIFACT_INVALID" and not menu.state.get("task")
    monkeypatch.setattr("builtins.input", lambda *a: str(source))
    monkeypatch.setattr(menu, "password", lambda *a: pytest.fail("unexpected source login"))
    with monkeypatch.context() as setup:
        setup.setattr(menu, "continue_task", lambda: None)
        menu.start(False)
    task = Path(menu.state["task"])
    local = task / "bundle"
    assert verify(local, "CaptureBundle") == material_bundle.manifest
    assert not (local / "unlisted.txt").exists()
    assert read_json(task / "task.json")["fixed_bundle_hash"] == interactive.digest(material_bundle.manifest)
    (local / "queries.jsonl").write_text("changed")
    with pytest.raises(PCCError) as exc:
        menu.continue_task()
    assert exc.value.code == "HASH_MISMATCH"


def test_fixed_material_warmup_resumes_and_runs_only_once(config, material_bundle, tmp_path, monkeypatch):
    import shutil
    menu = interactive.Menu(config)
    task = tmp_path / "fixed"
    shutil.copytree(material_bundle.path, task / "bundle")
    state = {"profiles_hash": interactive.digest(menu.profiles), "targets": ["old", "new"], "source": "prod",
             "options": {}, "imported": ["old", "new"], "fixed_bundle_hash": interactive.digest(material_bundle.manifest)}
    write_json(task / "task.json", state)
    menu.state["task"] = str(task)
    monkeypatch.setattr(menu, "password", lambda *a: None)
    calls = []
    def collect(function, request, **kwargs):
        assert function is api.compare
        out = Path(request.out)
        calls.append((out.name, request.resume))
        if len(calls) == 1:
            (task / ".warmup.partial").mkdir()
            return SimpleNamespace(run_status="interrupted")
        if out.name == "warmup":
            write_json(out / "summary.json", {"selected_samples": "1"})
            finalize(out, {"kind": "ComparisonResult"})
        return SimpleNamespace(summary={}, run_status="ok", exit_code=0)
    monkeypatch.setattr(menu, "core", collect)
    menu.continue_task()
    assert "report" not in read_json(task / "task.json")
    menu.continue_task()
    menu.continue_task()
    assert calls[:2] == [("warmup", False), ("warmup", True)]
    assert len(calls) == 4 and all(name.startswith("report-") for name, _ in calls[2:])


def test_cancel_import_and_resume_uses_saved_task(config, material_bundle, monkeypatch):
    menu = interactive.Menu(config)
    task = menu.new_dir("task")
    task.mkdir()
    import shutil
    shutil.copytree(material_bundle.path, task / "bundle")
    state = {"profiles_hash": interactive.digest(menu.profiles), "targets": ["old", "new"], "source": "prod",
             "options": {"source": "prod", "sql_file": "unused.jsonl"}, "imported": []}
    write_json(task / "task.json", state)
    menu.state["task"] = str(task)
    monkeypatch.setattr(menu, "core", lambda *a, **k: material_bundle)
    monkeypatch.setattr(menu, "password", lambda *a: None)
    monkeypatch.setattr(interactive, "validate_materials", lambda b: ["demo"])
    monkeypatch.setattr(interactive, "mysql_command", lambda *a: [])
    checks = []
    monkeypatch.setattr(interactive, "inspect_targets", lambda *a: checks.append(a) or {"old": "v6.5.12", "new": "v8.5.5"})
    monkeypatch.setattr(interactive, "import_target", lambda *a: pytest.fail("Import happened without IMPORT"))
    answers = iter(["1", "no"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    menu.continue_task()
    assert len(checks) == 1
    assert read_json(task / "task.json")["imported"] == []


def test_login_check_is_sql_only_and_reprompts_rejected_interactive_password(config, monkeypatch, capsys):
    from pcc_tool.capture.connection import DBConnectError
    menu = interactive.Menu(config)
    menu.profiles["prod"]["status_token_env"] = "PCC_UI_STATUS_ONLY"
    monkeypatch.delenv("PCC_DB_PASSWORD", raising=False)
    monkeypatch.delenv("PCC_UI_STATUS_ONLY", raising=False)
    prompts, attempts = [], []
    responses = iter(["incorrect-test-value", "correct-test-value"])
    monkeypatch.setattr(interactive.getpass, "getpass", lambda label: prompts.append(label) or next(responses))
    class Login:
        def __init__(self, profile):
            attempts.append(profile["host"])
        def __enter__(self):
            if os.environ["PCC_DB_PASSWORD"] == "incorrect-test-value":
                raise DBConnectError(1045 if attempts[-1] == "old.invalid" else 2003)
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(interactive, "Database", Login)
    try:
        menu.doctor()
        errors = capsys.readouterr().out.splitlines()
        assert len(errors) == 3 and "账号或密码" in errors[1] and "2003" in errors[0]
        assert "PCC_DB_PASSWORD" not in os.environ
        menu.doctor()
        assert capsys.readouterr().out.splitlines() == [f"{name}  登录成功" for name in ("prod", "old", "new")]
        assert attempts == ["prod.invalid", "old.invalid", "new.invalid"] * 2
        assert len(prompts) == 2 and all("Status" not in p for p in prompts)
        assert not any("test-value" in line for line in errors)
    finally:
        menu.close()


def test_import_both_then_compare_later_without_reimport(config, material_bundle, tmp_path, monkeypatch):
    import shutil
    menu = interactive.Menu(config)
    task = tmp_path / "later"
    shutil.copytree(material_bundle.path, task / "bundle")
    state = {"profiles_hash": interactive.digest(menu.profiles), "targets": ["old", "new"],
             "source": "prod", "options": {}, "imported": []}
    write_json(task / "task.json", state)
    menu.state["task"] = str(task)
    menu.save()
    monkeypatch.setattr(interactive.Menu, "password", lambda *args: None)
    monkeypatch.setattr(interactive, "mysql_command", lambda *args: [])
    monkeypatch.setattr(interactive, "inspect_targets", lambda *args: {"old": "v6.5.12", "new": "v8.5.5"})
    imported = []
    monkeypatch.setattr(interactive, "import_target", lambda prep, bundle, side, *args: imported.append(side))
    monkeypatch.setattr(menu, "core", lambda *args, **kwargs: pytest.fail("Started comparison before operator returned"))
    answers = iter(["3", "IMPORT"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    menu.continue_task()
    saved = read_json(task / "task.json")
    assert imported == saved["imported"] == ["old", "new"] and "report_pending" not in saved
    restarted = interactive.Menu(config)
    calls = []
    def compare(function, request, **kwargs):
        calls.append(function)
        return SimpleNamespace(summary={}, exit_code=0, run_status="completed")
    monkeypatch.setattr(restarted, "core", compare)
    restarted.continue_task()
    assert calls == [api.compare] and imported == ["old", "new"]


def test_terminal_color_respects_output_and_environment(monkeypatch):
    monkeypatch.setattr(interactive.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert interactive.paint("登录成功", "32") == "\033[32m登录成功\033[0m"
    monkeypatch.setenv("NO_COLOR", "")
    assert interactive.paint("登录成功", "32") == "登录成功"
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert interactive.paint("登录成功", "32") == "登录成功"
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(interactive.sys.stdout, "isatty", lambda: False)
    assert interactive.paint("登录成功", "32") == "登录成功"
