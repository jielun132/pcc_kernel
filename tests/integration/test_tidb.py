"""Opt-in read-only integration; provision/import the two isolated targets manually."""
import os
from pathlib import Path

import pytest

from pcc_tool.api import compare, diff, doctor
from pcc_tool.capture.connection import load_profiles
from pcc_tool.models import CompareRequest, DiffRequest, DoctorRequest

pytestmark = pytest.mark.integration


def test_real_prepared_targets(tmp_path):
    config, bundle = os.environ.get("PCC_TEST_CONFIG"), os.environ.get("PCC_TEST_BUNDLE")
    if os.environ.get("PCC_RUN_INTEGRATION") != "1" or not config or not bundle:
        pytest.skip("用户尚未提供并明确启用隔离 TiDB 环境")
    profiles = load_profiles(config)
    assert all(profiles[s].get("role") == "test" and profiles[s].get("expected_version") for s in ("old", "new"))
    readiness = doctor(DoctorRequest(("old", "new"), bundle), profiles=profiles)
    assert readiness["run_status"] == "completed", readiness["issues"]
    result = compare(CompareRequest(bundle, "old", "new", str(tmp_path / "online")), profiles=profiles)
    assert result.exit_code in {0, 1}, result.issues
    offline = diff(DiffRequest(result.output_dir, str(tmp_path / "offline")))
    assert result.summary == offline.summary
    assert result.exit_code == offline.exit_code
