"""Measure distinct synthetic cases. Output is evidence, not a TiDB throughput claim."""
import argparse
import json
import platform
import resource
import time
from pathlib import Path

from pcc_tool import api
from pcc_tool.artifacts.store import Publication
from pcc_tool.models import CaptureBundle, PlanSnapshot, QueryCase, VERSIONS, digest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, choices=[1000, 10000], required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    start = time.perf_counter()
    queries, snapshots = [], {}
    for i in range(args.count):
        q = QueryCase.create(f"SELECT id FROM app.orders WHERE id = {i}", "app")
        queries.append(q)
        for side in ("old", "new"):
            snapshots[q.query_id, side] = PlanSnapshot(q.query_id, side, version="synthetic",
                columns=["id", "estRows", "task", "access object", "operator info"],
                rows=[[f"Point_Get_{i + 1}", "1" if side == "old" else "2", "root", "table:orders", f"handle:{i}"]])
    assert len({q.sql_hash for q in queries}) == args.count
    result, irs = api._summarize(queries, snapshots, "benchmark", [])
    compute = time.perf_counter() - start
    bundle = CaptureBundle({"kind": "CaptureBundle", "contract_version": "1", "objects": []}, queries,
                          context={"fixture_origin": "distinct_synthetic_cases"})
    with Publication(args.out, digest({"count": args.count, "versions": VERSIONS})) as publication:
        api._write_result(publication.work, result, bundle, snapshots, irs, api.Events())
        publication.checkpoint()
        publication.publish({"kind": "ComparisonResult", "versions": VERSIONS, "run_status": result.run_status})
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    info = {"count": args.count, "distinct_sql_hashes": len({q.sql_hash for q in queries}),
            "compute_seconds": round(compute, 4), "total_seconds": round(time.perf_counter() - start, 4),
            "peak_rss_bytes": rss if platform.system() == "Darwin" else rss * 1024,
            "output_bytes": sum(f.stat().st_size for f in Path(args.out).rglob("*") if f.is_file()),
            "python": platform.python_version(), "platform": platform.platform(), "workload": "synthetic_offline_parse_diff_report"}
    print(json.dumps(info, sort_keys=True))


if __name__ == "__main__":
    main()
