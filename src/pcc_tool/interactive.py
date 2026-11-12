"""A terminal workflow over the public API; existing automation commands stay unchanged."""
from __future__ import annotations

import getpass
import os
import re
import resource
import secrets
import shlex
import shutil
import signal
import sys
import threading
from collections import Counter
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

from pcc_tool import __version__, api
from pcc_tool.artifacts.store import load_bundle, read_json, safe_path, source_lock, verify, write_bytes, write_json
from pcc_tool.capture.connection import Database, DBConnectError, load_profiles, profile_named
from pcc_tool.demo import generate
from pcc_tool.models import DEFAULT_CONCURRENCY, METRICS, CompareRequest, DiffRequest, ExportRequest, PCCError, PrepareRequest, digest
from pcc_tool.plan.rule_catalog import ASSESSMENT_VERSION
from pcc_tool.workflow import import_target, inspect_targets, mysql_command, validate_materials

DEFAULTS = {"source": "prod", "old": "old", "new": "new", "out_dir": "pcc-output", "top_n": 20,
            "order_by": list(METRICS), "ranking_scope": "global", "schemas": [],
            "statement_types": ["select", "update", "delete"], "stats_retries": 5, "concurrency": DEFAULT_CONCURRENCY,
            "timeout_seconds": 60, "mysql_client": "mysql", "sql_file": "", "begin": "", "end": "",
            "rules_config": "", "rule_profile": ""}


def settings(path):
    raw = read_json(path)
    value = raw.get("workflow", {})
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise PCCError("INVALID_CONFIG", "workflow 含未知字段")
    cfg = {**DEFAULTS, **value}
    for key in ("source", "old", "new"):
        if not isinstance(cfg[key], str) or not cfg[key].isidentifier():
            raise PCCError("INVALID_CONFIG", "workflow 环境名必须是标识符")
    for key in ("out_dir", "mysql_client", "sql_file", "begin", "end", "rules_config", "rule_profile"):
        if not isinstance(cfg[key], str) or (key in {"out_dir", "mysql_client"} and not cfg[key]):
            raise PCCError("INVALID_CONFIG", "workflow 路径或时间字段无效")
    for key, low, high in (("top_n", 1, 10000), ("stats_retries", 0, 5), ("concurrency", 1, 16), ("timeout_seconds", 1, 300)):
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise PCCError("INVALID_CONFIG", f"workflow.{key} 超出范围")
    for key in ("order_by", "schemas", "statement_types"):
        if not isinstance(cfg[key], list) or any(not isinstance(x, str) or not x for x in cfg[key]):
            raise PCCError("INVALID_CONFIG", f"workflow.{key} 必须是字符串数组")
    if (not cfg["order_by"] or set(cfg["order_by"]) - set(METRICS) or not cfg["statement_types"]
            or set(cfg["statement_types"]) - {"select", "update", "delete", "insert", "replace"}
            or cfg["ranking_scope"] not in {"global", "per_schema"}):
        raise PCCError("INVALID_CONFIG", "workflow 筛选参数无效")
    for key in ("out_dir", "sql_file", "rules_config"):
        if cfg[key]:
            cfg[key] = str((path.parent / cfg[key]).resolve())
    if "/" in cfg["mysql_client"]:
        cfg["mysql_client"] = str((path.parent / cfg["mysql_client"]).resolve())
    return cfg


def paint(text, style):
    if sys.stdout.isatty() and os.environ.get("TERM") != "dumb" and "NO_COLOR" not in os.environ:
        return f"\033[{style}m{text}\033[0m"
    return text


def ask(label, default=""):
    suffix = f" [{default}]" if default else ""
    return input(paint(label + suffix, "1;36") + ": ").strip() or default


def print_result(result):
    summary = result.summary
    print(f"一致 {summary.get('same', '0')}；差异 {summary.get('changed', '0')}；"
          f"无法比对 {summary.get('incomparable', '0')}；未运行 {summary.get('not_run', '0')}。PCC 返回码：{result.exit_code}")
    if assessment := summary.get("assessment"):
        print(f"规则预判（规则 v{ASSESSMENT_VERSION}）  变优 {assessment['better']}  基本等价 {assessment['equivalent']}  "
              f"变差 {assessment['worse']}  待复核 {assessment['review']}")
    if basis := summary.get("review_basis"):
        print(f"待复核原因（每条计一次）  计划可能提前读表 {basis['plan_evaluation']}  "
              f"仅有代价估算 {basis['model_only']}  缺 SQL 或视图定义 {basis['missing_definition']}  "
              f"差异证据不足 {basis['unresolved_difference']}")


def time_value(value, timezone):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return parsed.isoformat()
    except ValueError:
        raise PCCError("INVALID_TIME", "时间格式应为 YYYY-MM-DD HH:MM:SS 或带时区的 ISO 时间") from None


class Menu:
    def __init__(self, config):
        self.capture_label = "采集"
        self.profiles = load_profiles(str(config))
        self.cfg = settings(config)
        self.root = Path(self.cfg["out_dir"])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.index_path = self.root / "ui-state.json"
        self.state = read_json(self.index_path) if self.index_path.exists() else {}
        self.secrets = {}
        self.original_secrets = {}
        self.last_password = None
        self.cancel = threading.Event()

    def save(self):
        write_json(self.index_path, self.state)

    def password(self, names, *, sql_only=False):
        credentials = [("password_env", "数据库密码")]
        if not sql_only:
            credentials.append(("status_token_env", "Status 凭据"))
        for name in names:
            profile = profile_named(self.profiles, name)
            for key, label in credentials:
                reference = profile.get(key)
                if not reference:
                    continue
                rejected = False
                while True:
                    if reference in os.environ:
                        value = os.environ[reference]
                    else:
                        previous = self.last_password if key == "password_env" and not rejected else None
                        hint = "回车复用本次已输入的密码" if previous is not None else "首次输入，不能为空"
                        value = self.read_secret(f"{name} {label}（输入不回显；{hint}）: ")
                        if not value:
                            if previous is None:
                                print(f"尚无可复用的{label}，请隐藏输入；配置中的凭据字段是环境变量名，不是密码。")
                                continue
                            value = previous
                        self.set_secret(reference, value)
                    if key == "password_env" and not sql_only:
                        try:
                            with Database(profile):
                                pass
                        except DBConnectError as exc:
                            if exc.driver_code != 1045:
                                raise
                            self.set_secret(reference, None)
                            if self.last_password == value:
                                self.last_password = None
                            rejected = True
                            print(f"{name} 账号或密码被拒绝（1045），请重新输入密码；Ctrl+C 退出。")
                            continue
                    if key == "password_env":
                        self.last_password = value
                    break

    @staticmethod
    def read_secret(prompt):
        # getpass may fall back to echoed input; refuse that fallback.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return getpass.getpass(prompt)
            except getpass.GetPassWarning:
                raise PCCError("SECURE_INPUT_UNAVAILABLE", "当前终端不支持隐藏输入，请先设置密码环境变量") from None

    def set_secret(self, reference, value):
        if reference not in self.original_secrets:
            self.original_secrets[reference] = os.environ.get(reference)
        if value is None:
            os.environ.pop(reference, None)
        else:
            os.environ[reference] = value
        self.secrets[reference] = value

    def close(self):
        for reference, value in self.secrets.items():
            if os.environ.get(reference) == value:
                original = self.original_secrets[reference]
                if original is None:
                    os.environ.pop(reference, None)
                else:
                    os.environ[reference] = original
        self.secrets.clear()
        self.original_secrets.clear()
        self.last_password = None

    def new_dir(self, name):
        return self.root / (name + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3))

    def event(self, value):
        event = value["event"]
        if event == "capture_progress":
            pending = []
            stages = {"guard": "检查 SQL", "connect": "连接", "session": "设置会话", "prepare": "绑定参数",
                      "explain": "EXPLAIN", "warnings": "读取告警", "postcheck": "复核会话", "cancel_wait": "等待取消确认"}
            for item in value["active"]:
                case = item["case_id"]
                label = case if isinstance(case, str) and re.fullmatch(r"SQL-[0-9]{1,12}", case) else item["query_id"][:12]
                number = int(item["attempt"])
                phase = "首次" if number == 1 else f"统计复查 {number - 1}"
                pending.append(f"{item['side']} {label} {phase}/{stages[item['stage']]}（本阶段 {item.get('stage_seconds', item['seconds'])} 秒）")
            maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak_mib = maxrss // (1024 ** 2 if sys.platform == "darwin" else 1024)
            counts = "，".join(f"{s} {value['completed_by_side'][s]}/{value['per_side_total']}" for s in ("old", "new"))
            line = f"{self.capture_label} · 已处理 {value['completed']}/{value['total']} 份（{counts}） · 已用 {value['elapsed_seconds']} 秒 · PCC 峰值 {peak_mib} MiB"
            if pending and "running_by_side" in value:
                running = value["running_by_side"]
                line += f" · 在途 old {running['old']}/{value['side_limit']}、new {running['new']}/{value['side_limit']}"
            queued = [f"{s} {n}" for s, n in value["retry_queue"].items() if int(n)]
            if queued:
                line += " · 待统计复查 " + "，".join(queued)
            if pending:
                line += " · " + ("正在中断 " if value["cancelling"] else "当前等待 ") + "，".join(pending)
            print(paint(line, "36"), flush=True)
        elif event == "compare_stage":
            labels = {"precheck": "比较前核验材料", "postcheck": "采集后复核材料",
                      "diff": "正在比较计划和判定差异", "write_report": "正在写入报告"}
            print(paint(labels[value["stage"]] + " " + value.get("side", ""), "1;34"), flush=True)
        elif event in {"export_started", "export_completed", "compare_started"}:
            print(paint({"export_started": "正在导出 SQL、表结构和统计信息…", "export_completed": "材料导出完成",
                         "compare_started": "正在采集双端执行计划（Ctrl+C 保存进度）"}[event], "1;36"), flush=True)

    def core(self, function, request, **kwargs):
        self.cancel.clear()
        handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_: self.cancel.set())
        try:
            return function(request, on_event=self.event, cancel=self.cancel, **kwargs)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    def demo(self):
        out = self.new_dir("demo")
        result = generate(str(out))
        self.state["demo"] = str(out)
        self.save()
        print(paint(f"已生成 {result['case_count']} 条、{len(result['categories'])} 类案例。", "1;32"))
        profile = profile_named(self.profiles, self.cfg["source"])
        login = [self.cfg["mysql_client"], "--no-defaults", "--protocol=TCP", "--default-character-set=utf8mb4",
                 "--host=" + profile["host"], "--port=" + str(profile.get("port", 4000)),
                 "--user=" + profile["user"], "-p"]
        tls = profile.get("tls")
        login.append("--ssl-mode=" + ("VERIFY_IDENTITY" if tls is not None else "DISABLED"))
        login.extend(f"--ssl-{key}=" + tls[key] for key in ("ca", "cert", "key") if tls and tls.get(key))
        print("在另一终端登录独立的模拟生产环境：")
        print(shlex.join(login))
        print("登录后执行一次：")
        print("source " + str(out / "setup.sql") + ";")
        print("完成后回到本菜单选择 3，SQL 文件路径直接回车。")
        print("案例索引：" + str(out / "CASES.md"))

    def start(self, from_summary):
        cfg = self.cfg
        source = cfg["source"]
        profile = profile_named(self.profiles, source)
        options = {"source": source, "statement_types": tuple(cfg["statement_types"])}
        reference = None
        if from_summary:
            timezone = profile.get("timezone", "Asia/Shanghai")
            print(f"时区 {timezone}；每榜 Top {cfg['top_n']}；指标 {', '.join(cfg['order_by'])}")
            begin = time_value(ask("开始时间", cfg["begin"]), timezone)
            end = time_value(ask("结束时间", cfg["end"]), timezone)
            if datetime.fromisoformat(begin) >= datetime.fromisoformat(end):
                raise PCCError("INVALID_TIME", "结束时间必须晚于开始时间")
            default_schemas = ",".join(cfg["schemas"])
            label = "库名（逗号分隔；回车用默认，输入 * 采集全部库）" if default_schemas else "库名（逗号分隔；回车或 * 采集全部库）"
            selected = ask(label, default_schemas)
            schemas = () if selected == "*" else tuple(x.strip() for x in selected.split(",") if x.strip())
            if "*" in schemas:
                raise PCCError("INVALID_CONFIG", "采集全部库请单独输入 *，不要与库名混填")
            options.update(from_summary=True, begin=begin, end=end, schemas=schemas, top_n=cfg["top_n"],
                           order_by=tuple(cfg["order_by"]), ranking_scope=cfg["ranking_scope"])
        else:
            default = cfg["sql_file"]
            if self.state.get("demo"):
                default = str(Path(self.state["demo"]) / "queries.jsonl")
            file = Path(ask("SQL / JSONL 文件或固定材料目录", default)).expanduser().resolve()
            if file.is_dir():
                reference = load_bundle(str(file))
                print(f"使用固定材料中的 {len(reference.queries)} 条 SQL、表结构和源统计信息。")
            else:
                if not file.is_file():
                    raise PCCError("SQL_FILE_INVALID", "SQL 文件不存在")
                if self.state.get("demo") and file == Path(self.state["demo"]) / "queries.jsonl":
                    verify(file.parent, "DemoCases")
                options.update(sql_file=str(file), statement_types=("select", "update", "delete", "insert", "replace"))
                print("文件模式全量比较，不按 Top N 截取；业务 DML 仅做 EXPLAIN。")
        if reference is None:
            self.password((source,))
        task = self.new_dir("task")
        task.mkdir(mode=0o700)
        if reference is not None:
            target = task / "bundle"
            target.mkdir(mode=0o700)
            for name in reference.manifest["files"]:
                dst = safe_path(target, name)
                dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(safe_path(Path(reference.path), name), dst)
            write_json(target / "manifest.json", reference.manifest)
            verify(target, "CaptureBundle")
        state = {"profiles_hash": digest(self.profiles), "targets": [cfg["old"], cfg["new"]],
                 "source": source, "options": options, "imported": []}
        if reference is not None:
            state["fixed_bundle_hash"] = digest(reference.manifest)
        write_json(task / "task.json", state)
        self.state["task"] = str(task)
        self.save()
        self.continue_task()

    def continue_task(self):
        if not self.state.get("task"):
            print("尚无任务，请先选择 2 或 3。")
            return
        from pcc_tool.plan.rule_config import load_policy
        load_policy(self.cfg["rules_config"] or None,
                    self.state.get("rule_profile") or self.cfg["rule_profile"] or None)
        task = Path(self.state["task"])
        state = read_json(task / "task.json")
        if state["profiles_hash"] != digest(self.profiles):
            raise PCCError("CONFIG_CHANGED", "连接配置已改变；请恢复原配置或新建任务")
        source = state["source"]
        old, new = state["targets"]
        bundle_path, prepared = task / "bundle", task / "prepared"
        if not bundle_path.exists():
            if state.get("fixed_bundle_hash"):
                raise PCCError("ARTIFACT_INVALID", "固定复测材料缺失，请重新选择原材料目录")
            if bundle_path.with_name("." + bundle_path.name + ".partial").exists():
                raise PCCError("EXPORT_INCOMPLETE", "上次导出未完成，材料已保留；请选择 2 或 3 新建任务")
            self.password((source,))
            options = dict(state["options"])
            if "top_n" in options:
                options["top_n"] = int(options["top_n"])
            bundle = self.core(api.export, ExportRequest(str(bundle_path), **options), profiles=self.profiles)
        else:
            bundle = load_bundle(str(bundle_path))
            if state.get("fixed_bundle_hash") and state["fixed_bundle_hash"] != digest(bundle.manifest):
                raise PCCError("HASH_MISMATCH", "本次任务的固定复测材料已变化")
        if bundle.issues:
            counts = Counter(i["code"] for i in bundle.issues)
            print("材料存在待核验项，将原样保留在报告：" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        if not prepared.exists():
            api.prepare(PrepareRequest(str(bundle_path), (old, new), str(prepared)), profiles=self.profiles)
        groups = bundle.selection.get("selected_digests")
        prefix = f"榜单去重后 {groups} 个 SQL 指纹，" if groups is not None else ""
        print(f"{prefix}本次 {len(bundle.queries)} 条 SQL 样例，双端共 {2 * len(bundle.queries)} 份。材料目录：{prepared}")
        pending = [name for name in (old, new) if name not in state["imported"]]
        if pending:
            choice = ask("1 导入并比较  2 已人工导入，仅比较  3 只导入两端，稍后比较  0 返回", "1")
            if choice == "0":
                return
            if choice not in {"1", "2", "3"}:
                raise PCCError("INVALID_CHOICE", "请选择 0、1、2 或 3")
            if choice in {"1", "3"}:
                schemas = validate_materials(bundle)
                for name in pending:
                    mysql_command(profile_named(self.profiles, name), self.cfg["mysql_client"])
                fixed = bool(state.get("fixed_bundle_hash"))
                source_identity = (bundle.context.get("status_identity") or {}) if fixed else None
                self.password((old, new) if fixed else (source, old, new))
                versions = inspect_targets(self.profiles, source, old, new, schemas, pending, source_identity)
                for name in pending:
                    p = self.profiles[name]
                    print(f"导入 {name}: {p['host']}:{p.get('port', 4000)}，实际版本 {versions[name]}")
                print("创建库：" + ", ".join(schemas))
                print("只导入表结构和统计信息。本次待导入的目标均已通过空库检查。")
                if ask("输入 IMPORT 确认，其他输入返回") != "IMPORT":
                    return
                # Recheck after user input, since the confirmation can remain open indefinitely.
                inspect_targets(self.profiles, source, old, new, schemas, pending, source_identity)
                for name in pending:
                    state["importing"] = name
                    write_json(task / "task.json", state)
                    import_target(str(prepared), str(bundle_path), name, self.profiles[name], self.cfg["mysql_client"])
                    state["imported"].append(name)
                    state.pop("importing", None)
                    write_json(task / "task.json", state)
                    print(paint(name + " 导入完成", "32"), flush=True)
                if choice == "3":
                    print(paint("两端已导入。准备好后选主菜单 4，核验两端材料并开始比较。", "1;32"))
                    return
        self.password((old, new))
        def collect(report):
            partial = report.with_name("." + report.name + ".partial")
            count = self.cfg["concurrency"]
            self.capture_label = "预热" if report.name == "warmup" else "正式采集"
            print(paint(f"{self.capture_label} · 从首批开始 old/new 每端最多 {count} 份并发，跨表调度", "1;34"))
            return self.core(api.compare, CompareRequest(str(bundle_path), old, new, str(report), resume=partial.exists(),
                             stats_retries=0 if report.name == "warmup" else self.cfg["stats_retries"], concurrency=count,
                             warmup_only=report.name == "warmup",
                             rules_config=self.cfg["rules_config"] or None,
                             rule_profile=self.state.get("rule_profile") or self.cfg["rule_profile"] or None,
                             timeout_seconds=self.cfg["timeout_seconds"]), profiles=self.profiles)

        warmup = task / "warmup"
        if not warmup.exists():
            print(paint("先在两端执行一轮普通 EXPLAIN 加载统计信息；预热不参与计划比较。", "1;34"))
            observation = collect(warmup)
            if observation.run_status == "interrupted":
                print("已保存预热进度，选择 4 继续。")
                return
            print(f"预热观测已保存到 {warmup}。开始正式采集。")
        else:
            # 0.1.6 stored its fixed-material warmup as a ComparisonResult.
            if verify(warmup).get("kind") not in {"PlanWarmup", "ComparisonResult"}:
                raise PCCError("ARTIFACT_INVALID", "预热材料类型不正确")
        if state.get("report_pending"):
            report = Path(state["report_pending"])
        else:
            report = task / ("report-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3))
            state["report_pending"] = str(report)
            write_json(task / "task.json", state)
        result = collect(report)
        print_result(result)
        if result.run_status == "interrupted":
            print("已保存比较检查点，选择 4 继续。")
            return
        state.pop("report_pending", None)
        state["report"] = str(report)
        write_json(task / "task.json", state)
        self.state["report"] = str(report)
        self.save()
        print(paint("报告：" + str(report / "report.html"), "1;32"))
        if result.exit_code >= 2:
            print(paint("存在未完成项，请在报告中查看原因。", "33"))

    def reports(self):
        reports = sorted(self.root.glob("task-*/report-*/report.html"), reverse=True)
        for path in reports[:20]:
            print(path)
        if not reports:
            print("尚无报告。")

    def doctor(self):
        names = tuple(self.cfg[key] for key in ("source", "old", "new"))
        self.password(names, sql_only=True)
        rejected = set()
        for name in names:
            profile = self.profiles[name]
            try:
                with Database(profile):
                    pass  # The SQL handshake verifies the credentials.
            except PCCError as exc:
                reason = exc.message
                if isinstance(exc, DBConnectError) and exc.driver_code == 1045:
                    reason = "账号或密码被拒绝（1045）"
                    rejected.add(profile.get("password_env"))
                print(paint(f"{name}  连接失败 · {reason}", "31"), flush=True)
            else:
                print(paint(f"{name}  登录成功", "32"), flush=True)
        for ref in rejected & self.secrets.keys():
            if os.environ.get(ref) == self.secrets[ref]:
                if self.last_password == self.secrets[ref]:
                    self.last_password = None
                self.set_secret(ref, None)

    def offline(self):
        if not self.state.get("report"):
            print("尚无报告。")
            return
        out = self.new_dir("offline")
        result = self.core(api.diff, DiffRequest(self.state["report"], str(out),
                           rules_config=self.cfg["rules_config"] or None,
                           rule_profile=self.state.get("rule_profile") or self.cfg["rule_profile"] or None))
        print_result(result)
        print(f"离线报告：{out / 'report.html'}")

    def rule_profile(self):
        from pcc_tool.plan.rule_config import load_policy
        policy = load_policy(self.cfg["rules_config"] or None)
        print("0  按实际采集版本自动匹配")
        for i, profile in enumerate(policy["profiles"], 1):
            print(f"{i}  {profile['label']}  [{profile['id']}]")
        choice = ask("选择版本规则方案", "0")
        if not choice.isdigit() or not 0 <= int(choice) <= len(policy["profiles"]):
            raise PCCError("INVALID_CHOICE", "请选择显示的方案编号")
        self.state["rule_profile"] = "auto" if choice == "0" else policy["profiles"][int(choice) - 1]["id"]
        self.save()
        print("已选择规则方案；下次比较或离线重算生效。实际连接版本仍按 profiles 精确校验。")

    def loop(self):
        actions = {"1": self.demo, "2": lambda: self.start(True), "3": lambda: self.start(False),
                   "4": self.continue_task, "5": self.reports, "6": self.doctor, "7": self.offline, "8": self.rule_profile}
        while True:
            print("\n" + paint(f"PCC {__version__}  操作菜单", "1;36") + f"   old/new 各 {self.cfg['concurrency']} 份并发")
            for title, entries in (
                ("采集与比较", (("1", "生成演示 SQL 案例"), ("2", "按时间采集 Top SQL 并比较"), ("3", "从 SQL 文件全量比较"))),
                ("任务与工具", (("4", "继续上次任务"), ("5", "查看报告位置"), ("6", "检查数据库登录"), ("7", "离线重算上次报告"))),
                ("评审配置", (("8", "选择版本规则方案"),)),
            ):
                print(paint("  " + title, "1;34"))
                for key, label in entries:
                    print("  " + paint(key, "1;36") + "  " + label)
            print("  " + paint("0", "1") + "  退出")
            choice = ask("请选择", "0")
            if choice == "0":
                return 0
            if choice not in actions:
                print("请输入菜单中的编号。")
                continue
            try:
                actions[choice]()
            except PCCError as exc:
                print(paint(f"未完成 [{exc.code}]：{exc.message}", "31"))
            except MemoryError:
                print(paint("PCC 内存分配失败，请检查操作机可用内存。已落盘的比较进度可从菜单 4 继续。", "31"))
            except (OSError, ValueError, TypeError, KeyError):
                print("未完成：配置、任务记录或文件操作失败，请检查输入与权限。")


def run(config=None):
    path = Path(config or "pcc.json").expanduser().resolve()
    menu = None
    try:
        if not path.exists() and not config:
            write_bytes(path, (files("pcc_tool") / "demo_data" / "pcc.example.json").read_bytes())
            print(f"已生成配置模板：{path}\n请填写三个环境的连接和精确版本，再运行 ./pcc。密码在运行时隐藏输入。")
            return 0
        menu = Menu(path)
        # A second terminal cannot concurrently change this workflow's checkpoints.
        with source_lock(menu.root / ".interactive.lock"):
            return menu.loop()
    except (EOFError, KeyboardInterrupt):
        print("\n已退出；已有材料保留。已完成导出的任务可从菜单 4 继续。")
        return 4
    except PCCError as exc:
        print(f"未完成 [{exc.code}]：{exc.message}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print("配置或任务记录无效，请检查 JSON 与文件权限。", file=sys.stderr)
        return 2
    finally:
        if menu:
            menu.close()
