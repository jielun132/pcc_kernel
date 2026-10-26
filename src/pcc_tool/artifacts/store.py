from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from pcc_tool.models import (CONTRACT_VERSION, CaptureBundle, PCCError, QueryCase,
                             byte_hash, canonical, digest, now, primitive)

MAX_FILE_BYTES = 256 * 1024 * 1024


def read_json(path: Path):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError
        return json.loads(path.read_text("utf-8"), parse_float=__import__("decimal").Decimal)
    except (ValueError, OSError):
        raise PCCError("ARTIFACT_INVALID", f"无法读取 JSON: {path.name}") from None


def read_jsonl(path: Path) -> list[dict]:
    try:
        if path.is_symlink():
            raise ValueError
        rows = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if len(line) > MAX_FILE_BYTES:
                    raise ValueError
                if line.strip():
                    row = json.loads(line, parse_float=__import__("decimal").Decimal)
                    if not isinstance(row, dict):
                        raise ValueError
                    rows.append(row)
        return rows
    except (ValueError, OSError):
        raise PCCError("ARTIFACT_INVALID", f"无法读取 JSONL: {path.name}") from None


def sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.with_name(path.name + ".part").is_symlink():
        raise PCCError("UNSAFE_PATH", "拒绝向符号链接写入")
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as stream:
        os.chmod(temporary, 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def write_json(path: Path, value):
    write_bytes(path, (canonical(value) + "\n").encode())


def write_jsonl(path: Path, rows):
    write_bytes(path, "".join(canonical(row) + "\n" for row in rows).encode())


def safe_path(root: Path, name: str) -> Path:
    rel = PurePosixPath(name)
    if (not name or "\\" in name or rel.is_absolute() or re.match(r"^[A-Za-z]:", name)
            or any(p in {"..", ".", ""} for p in name.split("/"))):
        raise PCCError("UNSAFE_PATH", "材料路径越界")
    path = root / rel
    if not path.resolve().is_relative_to(root.resolve()):
        raise PCCError("UNSAFE_PATH", "材料路径越界")
    if root.is_symlink() or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise PCCError("UNSAFE_PATH", "材料包含符号链接")
    return path


def verify(root: Path, kind: str | None = None) -> dict:
    manifest = read_json(safe_path(root, "manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT_VERSION:
        raise PCCError("CONTRACT_UNSUPPORTED", "不支持该产物契约版本")
    if kind and manifest.get("kind") != kind:
        raise PCCError("ARTIFACT_INVALID", "材料类型不匹配")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise PCCError("ARTIFACT_INVALID", "缺少文件清单")
    for name, expected in files.items():
        path = safe_path(root, name)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES or byte_hash(path.read_bytes()) != expected:
            raise PCCError("HASH_MISMATCH", f"文件校验失败: {name}")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(files) | {"manifest.json"}:
        raise PCCError("ARTIFACT_INVALID", "目录含未登记文件")
    for p in root.rglob("*"):
        if p.is_symlink():
            raise PCCError("UNSAFE_PATH", "目录含符号链接")
    return manifest


def finalize(root: Path, manifest: dict) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PCCError("UNSAFE_PATH", "输出含符号链接")
        if path.is_file() and path != root / "manifest.json":
            files[path.relative_to(root).as_posix()] = byte_hash(path.read_bytes())
    result = {**manifest, "contract_version": CONTRACT_VERSION, "files": files}
    write_json(root / "manifest.json", result)
    verify(root)
    return result


@contextlib.contextmanager
def source_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PCCError("UNSAFE_PATH", "锁文件不能为符号链接")
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PCCError("SOURCE_LOCKED", "该输出目录已被另一个任务锁定") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Publication:
    """Retain interrupted work; never overwrite a published directory."""

    def __init__(self, output: str, fingerprint: str, resume=False):
        self.output = Path(output).absolute()
        self.work = self.output.with_name("." + self.output.name + ".partial")
        self.fingerprint, self.resume = fingerprint, resume
        self.lock = source_lock(self.output.with_name("." + self.output.name + ".lock"))

    def __enter__(self):
        self.lock.__enter__()
        try:
            if self.output.exists() or self.output.is_symlink():
                raise PCCError("OUTPUT_EXISTS", "输出目录已存在，请选择新目录")
            if self.work.exists():
                if not self.resume or self.work.is_symlink():
                    raise PCCError("OUTPUT_EXISTS", "存在中断产物，请使用 --resume 或新目录")
                state = read_json(self.work / "checkpoint.json")
                if state.get("fingerprint") != self.fingerprint:
                    raise PCCError("RESUME_MISMATCH", "输入、上下文或算法指纹变化，不能复用旧结果")
                for name, expected in state.get("files", {}).items():
                    if byte_hash(safe_path(self.work, name).read_bytes()) != expected:
                        raise PCCError("HASH_MISMATCH", "恢复检查点校验失败")
            else:
                self.work.mkdir(parents=True, mode=0o700)
                self.checkpoint()
            return self
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise

    def checkpoint(self, **extra):
        files = {p.relative_to(self.work).as_posix(): byte_hash(p.read_bytes())
                 for p in self.work.rglob("*") if p.is_file() and p not in {self.work / "checkpoint.json", self.work / "manifest.json"}
                 and not p.name.endswith(".part")}
        write_json(self.work / "checkpoint.json", {"fingerprint": self.fingerprint, "files": files, **extra})

    def publish(self, manifest: dict):
        finalize(self.work, {**manifest, "fingerprint": self.fingerprint})
        if self.output.exists():
            raise PCCError("OUTPUT_EXISTS", "输出目录已存在")
        os.rename(self.work, self.output)
        sync_directory(self.output.parent)

    def __exit__(self, *args):
        self.lock.__exit__(*args)


def load_bundle(path: str) -> CaptureBundle:
    root = Path(path)
    manifest = verify(root, "CaptureBundle")
    try:
        for obj in [*manifest.get("databases", []), *manifest.get("objects", [])]:
            for key, prefix in (("ddl_file", "metadata_backup/"), ("stats_file", "statistics_backup/")):
                ref = obj.get(key)
                if ref is not None and (not isinstance(ref, str) or ref not in manifest["files"] or not ref.startswith(prefix)):
                    raise ValueError
            if not isinstance(obj.get("schema"), str) or not obj["schema"]:
                raise ValueError
            if "table" in obj and (not isinstance(obj["table"], str) or obj.get("kind") not in {"table", "view"}):
                raise ValueError
        queries = [QueryCase(**row) for row in read_jsonl(root / "queries.jsonl")]
        ids = set()
        for q in queries:
            if q.query_id in ids or (q.sql_text and q.sql_hash != byte_hash(q.sql_text.encode())):
                raise ValueError
            expected = QueryCase.create(q.sql_text, q.schema, q.context, q.sample_origins[0] if q.sample_origins else {},
                                        parameters=q.parameters)
            if q.query_id != expected.query_id or q.executable_status not in {"ok", "needs_sample", "unsupported"}:
                raise ValueError
            if not isinstance(q.schema, str) or not isinstance(q.context, dict) or any(
                    not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, str) and v for v in pair) for pair in q.dependencies):
                raise ValueError
            ids.add(q.query_id)
        return CaptureBundle(manifest, queries, read_json(root / "context.json"),
                             read_json(root / "selection.json"), read_jsonl(root / "issues.jsonl"), str(root.absolute()))
    except (TypeError, ValueError, KeyError, AttributeError):
        raise PCCError("ARTIFACT_INVALID", "QueryCase 格式或 SQL hash 无效") from None


def safe_extract_zip(archive: str, destination: Path):
    """Validate the complete central directory before creating any output."""
    with zipfile.ZipFile(archive) as source:
        infos, names, total = source.infolist(), set(), 0
        for info in infos:
            name = info.filename.rstrip("/")
            safe_path(destination, name)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise PCCError("UNSAFE_ZIP", "ZIP 含符号链接或特殊文件")
            if name in names or info.flag_bits & 1:
                raise PCCError("UNSAFE_ZIP", "ZIP 含重复或加密条目")
            names.add(name)
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > 1024 * 1024 * 1024 or len(names) > 20000:
                raise PCCError("UNSAFE_ZIP", "ZIP 超过资源上限")
        destination.mkdir(parents=True, exist_ok=False)
        for info in infos:
            target = safe_path(destination, info.filename.rstrip("/"))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                write_bytes(target, source.read(info))
