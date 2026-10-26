import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from pcc_tool.artifacts.store import Publication, finalize, safe_extract_zip, safe_path, verify, write_json, write_jsonl
from pcc_tool.collector.legacy import read_tsv
from pcc_tool.collector.spool import publish_batch, read_spool
from pcc_tool.models import PCCError


def test_publication_nested_manifest_integrity_no_overwrite(tmp_path):
    output = tmp_path / "bundle"
    with Publication(str(output), "abc") as p:
        write_json(p.work / "inputs/manifest.json", {"test": "nested"})
        write_json(p.work / "data.json", {"data": 123})
        p.publish({"kind": "test"})
    assert "inputs/manifest.json" in verify(output)["files"]
    with pytest.raises(PCCError) as err:
        with Publication(str(output), "abc"):
            pass
    assert err.value.code == "OUTPUT_EXISTS"
    (output / "data.json").write_text("{}")
    with pytest.raises(PCCError) as err:
        verify(output)
    assert err.value.code == "HASH_MISMATCH"


def test_resume_checks_fingerprint_and_hash(tmp_path):
    out = str(tmp_path / "run")
    with Publication(out, "one") as p:
        write_json(p.work / "data.json", {"x": 1})
        p.checkpoint()
    with pytest.raises(PCCError, match="指纹"):
        with Publication(out, "two", resume=True):
            pass
    with Publication(out, "one", resume=True) as p:
        p.publish({"kind": "done"})
    assert verify(Path(out))["kind"] == "done"


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a/../../outside", "a\\..\\outside", "C:/absolute", "a//b", "a/./b"])
def test_paths_rejected(tmp_path, name):
    with pytest.raises(PCCError):
        safe_path(tmp_path, name)


@pytest.mark.parametrize("name,mode", [("../pwn", 0), ("/pwn", 0), ("link", stat.S_IFLNK | 0o777)])
def test_zip_validation_precedes_extraction(tmp_path, name, mode):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        z.writestr(info, "outside")
    out = tmp_path / "unpack"
    with pytest.raises(PCCError):
        safe_extract_zip(str(archive), out)
    assert not out.exists()


def test_spool_hash_and_idempotence(tmp_path, summary_row):
    root = tmp_path / "spool"
    a = publish_batch(root, "prod", [summary_row()], {"version": "v6.5.12"})
    b = publish_batch(root, "prod", [summary_row()], {"version": "v6.5.12"})
    assert a == b
    assert len(read_spool(str(root))[0]) == 1
    (root / "interrupted.part").write_text("junk")
    assert len(read_spool(str(root))[0]) == 1
    a.write_text(a.read_text() + "{}\n")
    with pytest.raises(PCCError):
        read_spool(str(root))


def test_legacy_19_columns_exact(tmp_path):
    from pcc_tool.collector.legacy import HEX_COLUMNS
    fields = ["prod", "2026-10-08 09:10:00", "0", "a:4000", "2026-10-08 09:00:00", "2026-10-08 09:30:00",
              "Select", "select ?", "select 1", "", "prev", "d", "app", "app.t", "p", "10", "100", "20", "1"]
    raw = [v.encode().hex() if i in HEX_COLUMNS else v for i, v in enumerate(fields)]
    path = tmp_path / "legacy.tsv"
    path.write_text("\t".join(raw) + "\n")
    row = read_tsv(path, "Asia/Shanghai")[0]
    assert row["query_sample_text"] == "select 1" and row["avg_total_keys"] is None
    assert row["summary_begin_time"].endswith("+08:00")
    path.write_text("\t".join(raw[:-1]))
    with pytest.raises(PCCError):
        read_tsv(path, "UTC")
