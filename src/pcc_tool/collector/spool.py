from pathlib import Path

from pcc_tool.artifacts.store import read_jsonl, write_bytes
from pcc_tool.models import CONTRACT_VERSION, PCCError, byte_hash, canonical, digest, now


def publish_batch(root: Path, source: str, rows: list[dict], metadata: dict) -> Path:
    payload = [{"contract_version": CONTRACT_VERSION, "kind": "SummaryBatch", "source": source,
                "metadata": metadata}, *rows]
    data = "".join(canonical(row) + "\n" for row in payload).encode()
    name = byte_hash(data) + ".ready.jsonl"
    path = root / name
    if not path.exists():
        write_bytes(path, data)
    return path


def read_spool(root: str, source: str | None = None) -> tuple[list[dict], list[dict]]:
    rows, metadata, batches = [], [], []
    for path in sorted(Path(root).glob("*.ready.jsonl")):
        if byte_hash(path.read_bytes()) != path.name.split(".")[0]:
            raise PCCError("HASH_MISMATCH", "持久化批次校验失败")
        batch = read_jsonl(path)
        if not batch or batch[0].get("contract_version") != CONTRACT_VERSION or batch[0].get("kind") != "SummaryBatch":
            raise PCCError("CONTRACT_UNSUPPORTED", "持久化批次格式不支持")
        if source and batch[0]["source"] != source:
            continue
        batches.append(batch)
    from .summary import IDENTITY
    batches.sort(key=lambda b: (b[0].get("metadata", {}).get("collected_at", ""), digest(b)))
    last_seen = {}
    for index, batch in enumerate(batches):
        metadata.append(batch[0])
        for raw in batch[1:]:
            row = dict(raw)
            identity = tuple(row.get(k, "") for k in IDENTITY)
            if identity in last_seen and last_seen[identity] < index - 1:
                row["counter_reappeared"] = True
            last_seen[identity] = index
            rows.append(row)
    return rows, metadata
