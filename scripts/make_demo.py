"""Generate clearly labelled synthetic examples through the real offline engine."""
import argparse
from pathlib import Path

from pcc_tool import api
from pcc_tool.artifacts.store import Publication
from pcc_tool.models import CaptureBundle, PlanSnapshot, QueryCase, VERSIONS, digest


def make_demo(out):
    queries = [QueryCase.create("SELECT * FROM app.orders WHERE id BETWEEN 10 AND 20", "app"),
               QueryCase.create("SELECT * FROM app.orders WHERE id=10", "app"),
               QueryCase.create(None, "app", origin={"source": "synthetic_missing_sample"}, executable_status="needs_sample")]
    plans = {}
    for side in ("old", "new"):
        rows = [["TableFullScan_1" if side == "old" else "TableRangeScan_7", "1000" if side == "old" else "11",
                 "cop[tikv]", "table:orders", "keep order:false" if side == "old" else "range:[10,20], keep order:false"]]
        for index, q in enumerate(queries[:2]):
            plans[q.query_id, side] = PlanSnapshot(q.query_id, side, version="synthetic-fixture",
                columns=["id", "estRows", "task", "access object", "operator info"],
                rows=rows if index == 0 else [["Point_Get_1", "1", "root", "table:orders", "handle:10"]])
    bundle = CaptureBundle({"kind": "CaptureBundle", "contract_version": "1", "objects": []}, queries,
                          context={"fixture_origin": "synthetic_demo_not_cluster_certification"},
                          selection={"kind": "demonstration", "business_coverage": "unknown"})
    fp = digest({"demo": queries, "versions": VERSIONS})
    result, irs = api._summarize(queries, plans, fp, [])
    with Publication(out, fp) as publication:
        api._write_result(publication.work, result, bundle, plans, irs, api.Events())
        publication.checkpoint()
        publication.publish({"kind": "ComparisonResult", "versions": VERSIONS, "run_status": result.run_status,
                             "fixture_origin": "synthetic_demo_not_cluster_certification"})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    make_demo(args.out)
