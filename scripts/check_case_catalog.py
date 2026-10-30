"""Enforce immutable case history locally and against the pull request / push base."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "tests/cases/sql_cases.jsonl"
LOCK = "tests/cases/catalog.lock.json"


def validate(catalog, lock, previous_catalog=b"", previous_lock=None):
    if not catalog.startswith(previous_catalog):
        raise ValueError("Historical cases were edited, removed or reordered; append new cases only")
    if not catalog.endswith(b"\n"):
        raise ValueError("Catalog must end with a newline")
    lines = catalog.splitlines(keepends=True)
    cases = [json.loads(line) for line in lines]
    ids = [case["id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate case IDs")
    expected = [dict(id=case["id"], sha256=hashlib.sha256(line).hexdigest()) for case, line in zip(cases, lines)]
    if lock != expected:
        raise ValueError("Catalog hashes differ from the lock; existing entries are immutable")
    if previous_lock is not None and lock[:len(previous_lock)] != previous_lock:
        raise ValueError("Historical lock entries were edited, removed or reordered")
    return len(cases)


def git_file(base, path):
    # Validate the revision separately: an unavailable base must not silently disable the check.
    subprocess.run(["git", "cat-file", "-e", base + "^{commit}"], cwd=ROOT, check=True, capture_output=True)
    result = subprocess.run(["git", "ls-tree", "--name-only", base, "--", path], cwd=ROOT, check=True, capture_output=True)
    if not result.stdout.strip():
        return None
    return subprocess.run(["git", "show", base + ":" + path], cwd=ROOT, check=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--base-auto", action="store_true")
    args = parser.parse_args()
    base = args.base
    if args.base_auto and os.environ.get("GITHUB_EVENT_PATH"):
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before") or base
        if set(base) == {"0"}:
            base = args.base
    previous = git_file(base, CATALOG) or b""
    old_lock = git_file(base, LOCK)
    count = validate((ROOT / CATALOG).read_bytes(), json.loads((ROOT / LOCK).read_text()),
                     previous, json.loads(old_lock) if old_lock else None)
    print(f"{count} SQL cases validated; historical cases and lock entries are unchanged")


if __name__ == "__main__":
    main()
