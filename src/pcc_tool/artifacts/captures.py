"""Durable final plans and the individual attempts awaiting statistics retries."""
from collections.abc import Mapping

from pcc_tool.artifacts.store import read_json, safe_path, write_bytes
from pcc_tool.capture.explain import has_unloaded_stats
from pcc_tool.models import PCCError, PlanSnapshot, byte_hash, canonical, digest


def retryable_stats(snapshot):
    return (snapshot.capture_status == "ok" and len(snapshot.attempts) == 1
            and {issue["code"] for issue in snapshot.issues} == {"STATS_NOT_READY"})


def _write_snapshot(folder, snapshot):
    data = canonical(snapshot).encode()
    path = folder / f"{snapshot.query_id}.{snapshot.side}.{byte_hash(data)}.json"
    write_bytes(path, data + b"\n")
    return path


def _read_checkpoint(path, query_ids):
    raw = read_json(path)
    if path.name.rsplit(".", 2)[-2] != digest(raw):
        raise PCCError("HASH_MISMATCH", "原始计划检查点被修改")
    snapshot = PlanSnapshot(**raw)
    if snapshot.query_id not in query_ids or snapshot.side not in {"old", "new"}:
        raise PCCError("ARTIFACT_INVALID", "恢复计划不属于本次输入")
    if path.name.rsplit(".", 2)[0] != f"{snapshot.query_id}.{snapshot.side}":
        raise PCCError("ARTIFACT_INVALID", "原始计划检查点的身份不一致")
    return snapshot


def _retry_context(snapshot):
    return digest([snapshot.version, *(snapshot.context.get(k) for k in
                   ("server_node", "session", "declared_session", "binding_policy"))])


class SavedPlans(Mapping):
    """Keep paths in memory; a deferred observation must never count as a completed side."""
    def __init__(self, root):
        self.directory = safe_path(root, "captures")
        self.directory.mkdir(exist_ok=True)
        self.retry_directory = safe_path(root, "stats-retries")
        self.paths = {}
        self.cache = {}
        self.deferred = {}

    def __len__(self):
        return len(self.paths)

    def __iter__(self):
        return iter(self.paths)

    def __contains__(self, key):
        return key in self.paths

    def __getitem__(self, key):
        path = self.paths[key]
        cached = self.cache.get(key[1])
        if not cached or cached[0] != path:
            cached = self.cache[key[1]] = (path, PlanSnapshot(**read_json(path)))
        return cached[1]

    def restore(self, query_ids, max_retries):
        for path in sorted(self.directory.glob("*.json")):
            snapshot = _read_checkpoint(path, query_ids)
            if snapshot.capture_status == "ok" and (snapshot.issues or not has_unloaded_stats(snapshot)):
                self.paths[snapshot.query_id, snapshot.side] = path
        pending = {}
        for path in self.retry_directory.glob("*.json"):
            snapshot = _read_checkpoint(path, query_ids)
            if not retryable_stats(snapshot):
                raise PCCError("ARTIFACT_INVALID", "统计复查检查点不含有效的等待记录")
            key = (snapshot.query_id, snapshot.side)
            number = str(snapshot.attempts[0].get("attempt"))
            if number not in {str(n) for n in range(1, max_retries + 1)}:
                raise PCCError("ARTIFACT_INVALID", "统计复查次数与当前配置不符")
            history = pending.setdefault(key, {})
            if int(number) in history:
                raise PCCError("ARTIFACT_INVALID", "统计复查检查点重复")
            history[int(number)] = path
        for key, history in pending.items():
            if sorted(history) != list(range(1, len(history) + 1)):
                raise PCCError("ARTIFACT_INVALID", "统计复查检查点缺少中间记录")
            if key not in self:
                self.deferred[key] = [history[n] for n in sorted(history)]

    def defer(self, snapshot):
        key = (snapshot.query_id, snapshot.side)
        history = self.deferred.setdefault(key, [])
        snapshot.attempts[0]["attempt"] = len(history) + 1
        history.append(_write_snapshot(self.retry_directory, snapshot))

    def save(self, snapshot):
        key = (snapshot.query_id, snapshot.side)
        self.paths[key] = _write_snapshot(self.directory, snapshot)

    def finish(self, snapshot):
        key = (snapshot.query_id, snapshot.side)
        paths = self.deferred.get(key, [])
        if paths:
            history = []
            drift = False
            final_context = _retry_context(snapshot)
            for path in paths:
                previous = _read_checkpoint(path, {snapshot.query_id})
                history.extend({**a, "version": previous.version, "context": previous.context}
                               for a in previous.attempts)
                if snapshot.capture_status == "ok" and _retry_context(previous) != final_context:
                    drift = True
            for i, attempt in enumerate(snapshot.attempts, len(history) + 1):
                attempt.update(attempt=i, version=snapshot.version, context=snapshot.context)
            snapshot.attempts = history + snapshot.attempts
            if drift:
                snapshot.issues.append({"code": "CONTEXT_DRIFT", "message": "统计复查期间目标节点、版本或会话上下文发生变化"})
        self.save(snapshot)
        self.deferred.pop(key, None)
