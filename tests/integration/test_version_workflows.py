"""Opt-in cross-cluster workflows on the approved synthetic fixture schema only."""
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sql_matrix import ALLOWED, CASES, SCHEMA
from pcc_tool import api
from pcc_tool.artifacts.store import load_bundle, verify, write_json, write_jsonl
from pcc_tool.capture.connection import Database, load_profiles, status_json
from pcc_tool.collector.dependencies import qualified, verify_materials
from pcc_tool.collector.spool import read_spool
from pcc_tool.models import CompareRequest, DiffRequest, ExportRequest, PCCError, PrepareRequest, digest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("PCC_RUN_VERSION_WORKFLOWS") != "1", reason="Three-cluster workflow is explicitly opt-in")]
LABELS = ("v653", "v6512", "enterprise")
PAIRS = [(a, b) for a in LABELS for b in LABELS if a != b]


@pytest.fixture(scope="module")
def live():
    profiles = load_profiles(os.environ["PCC_VERSION_CONFIG"])
    instances = json.loads(os.environ["PCC_TEST_INSTANCE_HOSTS"])
    for label in LABELS:
        hosts = instances[label]
        assert isinstance(hosts, list) and len(hosts) == 3
        assert all(isinstance(host, str) and host.strip() == host and host for host in hosts)
        assert len(set(hosts)) == 3, "Supply three distinct, approved TiDB instance hosts per cluster"
    root = Path(os.environ["PCC_LIVE_OUTPUT"])
    root.mkdir(parents=True, exist_ok=False)
    assert all(profiles[k].get("role") == "test" and profiles[k].get("expected_version") for k in LABELS)
    return profiles, root, instances


@pytest.fixture(scope="module")
def exports(live):
    profiles, root, _ = live
    results = {}
    for label in LABELS:
        # Native ANALYZE only restores this approved test fixture before a fresh
        # export. The resulting archive is never edited during import testing.
        with Database(profiles[label]) as db:
            db.version()
            for name in ("customers", "orders", "items", "partitioned_orders", "matrix_types"):
                db.query(f"ANALYZE TABLE {qualified(SCHEMA, name)}")
        sql_file = root / label / "queries.jsonl"
        write_jsonl(sql_file, CASES)
        full = api.export(ExportRequest(str(root / label / "bundle"), source=label, sql_file=str(sql_file), statement_types=ALLOWED), profiles=profiles)
        assert full.manifest["run_status"] == "completed", full.issues
        # Independent positive-control subset; full cases remain strictly tested
        # below and in the 115-case per-version matrix, including known failures.
        common_cases = [c for c in CASES if c["name"] not in {"partition_explicit", "long_prepared_256"}]
        write_jsonl(root / label / "common.jsonl", common_cases)
        common = api.export(ExportRequest(str(root / label / "common-bundle"), source=label,
            sql_file=str(root / label / "common.jsonl"), statement_types=ALLOWED), profiles=profiles)
        assert len(common.queries) == 113
        api.prepare(PrepareRequest(full.path, LABELS, str(root / label / "prepared")), profiles=profiles)
        results[label] = {"full": full, "common": common}
    return results


@pytest.fixture(scope="module")
def replay_results(live, exports):
    profiles, root, _ = live
    results = {}
    for source, target in PAIRS:
        pair_dir = root / f"{source}-to-{target}"
        full, common = exports[source]["full"], exports[source]["common"]
        statements = []
        for obj in full.manifest["objects"]:
            assert obj["schema"] == SCHEMA
            if obj.get("stats_file"):
                path = (Path(full.path) / obj["stats_file"]).resolve()
                payload = json.loads(path.read_bytes())
                assert payload["database_name"] == SCHEMA and payload["table_name"] == obj["table"]
                statements.append("LOAD STATS '" + str(path).replace("'", "''") + "';")
        imports = {}
        # The same immutable source package is restored into BOTH endpoints.
        for label in (source, target):
            profile = profiles[label]
            environment = dict(os.environ, MYSQL_PWD=os.environ.get(profile.get("password_env", ""), ""))
            process = subprocess.run(["mysql", "--local-infile=1", "--batch", "--host=" + profile["host"],
                "--port=" + str(profile["port"]), "--user=" + profile["user"]], env=environment,
                input="\n".join(statements), text=True, capture_output=True, timeout=120)
            assert process.returncode == 0, process.stderr
            with Database(profile) as db:
                state, issues = verify_materials(db, full.manifest)
            imports[label] = {"exit_code": process.returncode, "issues": issues, "state": state}
        write_json(pair_dir / "imports.json", imports)
        results[source, target] = {"imports": imports}
        for name, bundle in (("full", full), ("common", common)):
            comparison = api.compare(CompareRequest(bundle.path, source, target, str(pair_dir / name)), profiles=profiles)
            offline = api.diff(DiffRequest(comparison.output_dir, str(pair_dir / (name + "-offline"))))
            results[source, target][name] = {"exit_code": comparison.exit_code, "summary": comparison.summary,
                "issues": comparison.issues, "offline_equal": offline.summary == comparison.summary
                and verify(Path(offline.output_dir))["business_fingerprint"] == verify(Path(comparison.output_dir))["business_fingerprint"]}
        write_json(pair_dir / "result.json", results[source, target])
    return results


@pytest.mark.parametrize("source,target", PAIRS)
def test_nonpartition_import_and_common_sql_compare(replay_results, source, target):
    result = replay_results[source, target]
    for imported in result["imports"].values():
        ordinary = [i for i in imported["issues"] if i.get("object") != [SCHEMA, "partitioned_orders"]]
        assert not ordinary, ordinary
    assert result["common"]["exit_code"] in (0, 1), result["common"]
    assert result["common"]["summary"]["full_comparisons"] == "113"
    assert result["common"]["offline_equal"]


@pytest.mark.parametrize("source,target", PAIRS)
def test_cross_version_offline_parity_including_failures(replay_results, source, target):
    result = replay_results[source, target]
    assert result["common"]["offline_equal"]
    assert result["full"]["offline_equal"]


@pytest.mark.parametrize("source,target", PAIRS)
def test_full_import_including_partition_and_prepared_in(replay_results, source, target):
    result = replay_results[source, target]
    assert all(not imported["issues"] for imported in result["imports"].values()), result["imports"]
    assert result["full"]["exit_code"] in (0, 1), result["full"]
    assert result["full"]["summary"]["full_comparisons"] == "115"
    assert result["full"]["offline_equal"]


@pytest.mark.parametrize("label", LABELS)
def test_window_export_and_durable_collection_restart(live, label):
    profiles, root, instances = live
    folder = root / (label + "-persistence")
    folder.mkdir()
    profile = profiles[label]
    marker = "pcc_durable_" + digest(str(folder))[:12]
    sql = f"SELECT /*+ USE_INDEX(orders,idx_amount) */ SUM(amount) AS {marker} FROM orders WHERE amount>100"
    begin = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    for host, count in zip(instances[label], (3, 5, 7), strict=True):
        with Database(dict(profile, host=host)) as db:
            db.query(f"USE `{SCHEMA}`")
            for _ in range(count):
                db.query(sql)
    spool = folder / "spool"
    command = [sys.executable, "-m", "pcc_tool", "--config", os.environ["PCC_VERSION_CONFIG"],
               "--events", "jsonl", "collect", "--source", label, "--out", str(spool)]
    with (folder / "watch.stdout.jsonl").open("w") as stdout, (folder / "watch.stderr.log").open("w") as stderr:
        child = subprocess.Popen(command + ["--watch", "--interval-seconds", "1"], stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 30
        try:
            while len(list(spool.glob("*.ready.jsonl"))) < 2 and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert len(list(spool.glob("*.ready.jsonl"))) >= 2
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
            code = child.wait(timeout=40)
    assert code == 4
    first_files = {p.name for p in spool.glob("*.ready.jsonl")}
    restart = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert restart.returncode == 0, restart.stderr
    assert first_files < {p.name for p in spool.glob("*.ready.jsonl")}
    rows, metadata = read_spool(str(spool))
    assert rows and len(metadata) >= 3
    end = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    request = ExportRequest(str(folder / "window"), source=label, from_summary=True, begin=begin, end=end,
                            top_n=10000, schemas=(SCHEMA,), statement_types=ALLOWED)
    direct = api.export(request, profiles=profiles)
    offline = api.export(dataclasses.replace(request, out=str(folder / "from-spool"), from_summary=False,
        spool=str(spool), source=None, dependencies_bundle=direct.path), profiles={})
    counts = []
    for bundle in (direct, offline):
        matches = [q for q in bundle.queries if q.sql_text and marker in q.sql_text]
        assert len(matches) == 1 and matches[0].sql_text == sql
        assert matches[0].metrics["execution_count"] == "15"
        assert set(matches[0].metrics["ranks"]) == {"execution_count","total_latency","average_latency","total_keys","average_keys"}
        assert bundle.selection["window_semantics"] == "overlapping_cumulative_windows"
        counts.append(matches[0].metrics)
    # No current database connection or credentials are needed to consume saved
    # SQL and previously captured dependencies; all SQL records survive reread.
    assert [(q.query_id,q.sql_text,q.parameters) for q in load_bundle(offline.path).queries] == [(q.query_id,q.sql_text,q.parameters) for q in offline.queries]
    prepared = api.prepare(PrepareRequest(offline.path, (label,), str(folder / "prepared")), profiles=profiles)
    assert prepared["execution"] == "manual_only"
    with pytest.raises(PCCError) as error:
        api.export(dataclasses.replace(request, out=str(folder / "outside-window"), from_summary=False, spool=str(spool),
            source=None, dependencies_bundle=direct.path, begin="2000-01-01T00:00:00Z", end="2000-01-02T00:00:00Z"), profiles={})
    assert error.value.code == "NO_QUERIES_SELECTED"
    write_json(folder / "result.json", {"watch_exit": code, "restart_exit": restart.returncode,
        "batches": len(metadata), "metrics": counts, "direct_status": direct.manifest["run_status"],
        "offline_status": offline.manifest["run_status"], "direct_issues": direct.issues,
        "source_collection": direct.context.get("collection"), "outside_window": error.value.code})
