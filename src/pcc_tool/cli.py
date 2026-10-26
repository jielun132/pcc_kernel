from __future__ import annotations

import argparse
import json
import signal
import sys
import threading

from pcc_tool import __version__, api
from pcc_tool.capture.connection import load_profiles
from pcc_tool.models import (METRICS, CollectRequest, CompareRequest, ComparisonResult, DiffRequest,
                            DoctorRequest, ExportRequest, PCCError, PrepareRequest, canonical)


def comma(value):
    return tuple(s.strip() for s in value.split(",") if s.strip())


def parser():
    p = argparse.ArgumentParser(prog="pcc", description="TiDB SQL 采集与执行计划比较核心")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="连接配置 JSON；只允许密码环境变量引用")
    p.add_argument("--events", choices=["jsonl"], help="stdout 只输出版本化事件")
    commands = p.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="只读检查运行包、连接与材料")
    doctor.add_argument("--bundle")
    doctor.add_argument("--targets", type=comma, default=())
    collect = commands.add_parser("collect", help="采集累计快照到原子文件队列")
    collect.add_argument("--source", required=True)
    collect.add_argument("--watch", action="store_true")
    collect.add_argument("--interval-seconds", type=int, default=300)
    collect.add_argument("--out", required=True)
    export = commands.add_parser("export", help="筛选 SQL 并导出 CaptureBundle")
    export.add_argument("--source")
    inputs = export.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--from-summary", action="store_true")
    for name in ("spool", "repository", "legacy-package", "sql-file"):
        inputs.add_argument("--" + name)
    export.add_argument("--dependencies-bundle")
    export.add_argument("--begin")
    export.add_argument("--end")
    export.add_argument("--top-n", type=int, default=20)
    export.add_argument("--order-by", type=comma, default=METRICS)
    export.add_argument("--ranking-scope", choices=["global", "per_schema"], default="global")
    export.add_argument("--schemas", type=comma, default=())
    export.add_argument("--statement-types", type=comma, default=("select", "update", "delete"))
    export.add_argument("--out", required=True)
    prepare = commands.add_parser("prepare", help="生成 DBA 人工导入材料，不执行写库")
    prepare.add_argument("--bundle", required=True)
    prepare.add_argument("--targets", type=comma, required=True)
    prepare.add_argument("--out", required=True)
    compare = commands.add_parser("compare", help="在两套隔离环境中普通 EXPLAIN 并比较")
    compare.add_argument("--bundle", required=True)
    compare.add_argument("--old", required=True)
    compare.add_argument("--new", required=True)
    compare.add_argument("--out", required=True)
    compare.add_argument("--resume", action="store_true")
    compare.add_argument("--concurrency", type=int, default=2)
    compare.add_argument("--timeout-seconds", type=int, default=30)
    compare.add_argument("--stats-retries", type=int, default=1)
    compare.add_argument("--binding-policy", choices=["exclude", "preserve"], default="exclude")
    compare.add_argument("--session", type=json.loads, default={}, help="白名单会话变量 JSON，不接受任意 SET")
    diff = commands.add_parser("diff", help="完全离线重算归档计划")
    diff.add_argument("--result-dir", required=True)
    diff.add_argument("--out", required=True)
    diff.add_argument("--resume", action="store_true")
    return p


def main(argv=None):
    args = vars(parser().parse_args(argv))
    command, config, events_mode = args.pop("command"), args.pop("config"), args.pop("events")
    cancel = threading.Event()
    original = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            original[sig] = signal.signal(sig, lambda *_: cancel.set())

    def event(value):
        if events_mode:
            print(canonical(value), flush=True)
        else:
            print(value["event"], file=sys.stderr, flush=True)

    try:
        # Offline diff never resolves credentials or even opens --config.
        profiles = {} if command == "diff" else load_profiles(config)
        if command == "compare":
            result = api.compare(CompareRequest(**args), profiles=profiles, on_event=event, cancel=cancel)
        elif command == "diff":
            result = api.diff(DiffRequest(**args), on_event=event, cancel=cancel)
        elif command == "export":
            result = api.export(ExportRequest(**args), profiles=profiles, on_event=event, cancel=cancel)
        elif command == "collect":
            result = api.collect(CollectRequest(**args), profiles=profiles, on_event=event, cancel=cancel)
        elif command == "prepare":
            result = api.prepare(PrepareRequest(**args), profiles=profiles, on_event=event)
        else:
            result = api.doctor(DoctorRequest(**args), profiles=profiles, on_event=event)
        if isinstance(result, ComparisonResult):
            code = result.exit_code
            payload = {"run_status": result.run_status, "summary": result.summary, "output_dir": result.output_dir}
        elif command == "export":
            code = 3 if result.manifest["run_status"] == "partial" else 0
            payload = {"run_status": result.manifest["run_status"], "output_dir": result.path}
        else:
            payload = result
            code = 4 if result.get("run_status") == "interrupted" else 3 if result.get("run_status") == "partial" else 0
        if not events_mode:
            print(canonical(payload))
        else:
            event({"contract_version": "1", "event": "command_result", "exit_code": str(code), **payload})
        return code
    except PCCError as exc:
        payload = {"contract_version": "1", "event": "error", "code": exc.code, "message": exc.message}
        if events_mode:
            event(payload)
        else:
            print(canonical(payload), file=sys.stderr)
        return 4 if exc.code == "INTERRUPTED" else 2
    except (OSError, ValueError, TypeError):
        # Raw exceptions may contain credentials, SQL or network addresses.
        payload = {"contract_version": "1", "event": "error", "code": "INPUT_OR_IO_ERROR",
                   "message": "输入格式或文件操作失败，请检查参数、权限与材料契约"}
        if events_mode:
            event(payload)
        else:
            print(canonical(payload), file=sys.stderr)
        return 2
    finally:
        for sig, handler in original.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
