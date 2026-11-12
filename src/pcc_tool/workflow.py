"""Checks and MySQL client import used only by the explicitly confirmed UI action."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from pcc_tool.artifacts.store import load_bundle, read_json, safe_path, verify
from pcc_tool.capture.connection import Database, profile_named
from pcc_tool.capture.guard import SYSTEM_SCHEMAS
from pcc_tool.models import PCCError, digest


def validate_materials(bundle):
    """Only single CREATE statements for the declared, non-system objects."""
    manifest = bundle.manifest
    schemas = {obj["schema"] for obj in manifest.get("databases", [])}
    if not schemas or any(s.lower() in SYSTEM_SCHEMAS for s in schemas):
        raise PCCError("IMPORT_UNSAFE", "自动导入仅支持非系统库的完整建库材料")
    objects = manifest.get("objects", [])
    for obj in [*manifest.get("databases", []), *objects]:
        sql = safe_path(Path(bundle.path), obj["ddl_file"]).read_text("utf-8")
        try:
            tokens = sqlglot.tokenize(sql, read="mysql")
            if any(t.token_type == TokenType.SEMICOLON for t in tokens[:-1]):
                raise ValueError("Multiple statements")
            parsed_sql = sql
            if obj.get("kind") == "table":
                # SHOW CREATE's RANGE partition clause is outside SQLGlot's grammar.
                # A single CREATE TABLE is still checked for its exact target and
                # cannot contain AS SELECT / LIKE or a second statement.
                if any(t.token_type in {TokenType.SELECT, TokenType.LIKE} for t in tokens):
                    raise ValueError("Not a structural CREATE TABLE")
                depth = 0
                for token in tokens:
                    if depth == 0 and token.text.upper() == "PARTITION BY":
                        parsed_sql = sql[:token.start]
                        break
                    if token.token_type == TokenType.L_PAREN:
                        depth += 1
                    elif token.token_type == TokenType.R_PAREN:
                        depth -= 1
            trees = [t for t in sqlglot.parse(parsed_sql, read="mysql") if t is not None]
            tree = trees[0]
            target = tree.this.this if isinstance(tree.this, exp.Schema) else tree.this
            kind = tree.args.get("kind", "").upper()
            expected_kind = obj.get("kind", "database").upper()
            valid = (len(trees) == 1 and isinstance(tree, exp.Create) and isinstance(target, exp.Table)
                     and kind == expected_kind and not tree.args.get("replace")
                     and not tree.args.get("exists") and obj["schema"] in schemas
                     and target.name == obj.get("table", obj["schema"])
                     and target.db in {"", obj["schema"]}
                     and (kind == "VIEW" or tree.args.get("expression") is None))
        except Exception:
            valid = False
        if not valid:
            raise PCCError("IMPORT_UNSAFE", "DDL 超出自动导入范围；请审核生成的 SQL 后人工导入")
    return sorted(schemas)


def cluster_identity(nodes):
    if not isinstance(nodes, list):
        raise PCCError("TARGET_IDENTITY_UNKNOWN", "无法确认集群节点身份，拒绝自动导入")
    identity = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise PCCError("TARGET_IDENTITY_UNKNOWN", "集群节点身份材料无效，拒绝自动导入")
        kind = str(node.get("TYPE", "")).lower()
        if kind in {"tidb", "pd"}:
            address = node.get("STATUS_ADDRESS") or node.get("INSTANCE")
            if not isinstance(address, str) or not address.strip():
                raise PCCError("TARGET_IDENTITY_UNKNOWN", "集群节点地址缺失，拒绝自动导入")
            identity.add((kind, address.lower()))
    if not identity:
        raise PCCError("TARGET_IDENTITY_UNKNOWN", "无法确认集群节点身份，拒绝自动导入")
    return identity


def inspect_targets(profiles, source, old, new, schemas, pending, source_identity=None):
    names = (source, old, new)
    if len(set(names)) != 3:
        raise PCCError("TARGET_NOT_ISOLATED", "source、old、new 必须是三个独立环境")
    chosen = {name: profile_named(profiles, name) for name in (names if source_identity is None else (old, new))}
    if any(chosen[name].get("role") != "test" for name in (old, new)):
        raise PCCError("TARGET_NOT_ISOLATED", "导入目标必须明确配置 role=test")
    if any(not p.get("expected_version") for p in chosen.values()):
        raise PCCError("VERSION_REQUIRED", "自动导入前请为需要连接的环境填写精确 expected_version")
    endpoints = {(p["host"].lower(), int(p.get("port", 4000))) for p in chosen.values()}
    if len(endpoints) != len(chosen):
        raise PCCError("TARGET_NOT_ISOLATED", "三个环境不能使用相同 SQL 地址")
    identities, versions = {}, {}
    if source_identity is not None:
        if (not isinstance(source_identity, dict) or source_identity.get("verified") is not True
                or not isinstance(source_identity.get("node"), dict)
                or str(source_identity["node"].get("TYPE", "")).lower() != "tidb"
                or not isinstance(source_identity.get("cluster_nodes"), list)
                or source_identity["node"] not in source_identity.get("cluster_nodes", [])):
            raise PCCError("SOURCE_IDENTITY_MISSING", "固定材料缺少完整源集群身份；请用修复版重新导出，或人工导入后选择仅比较")
        identities[source] = cluster_identity(source_identity["cluster_nodes"])
    # Finish all checks before the first write, including the second target.
    for name, profile in chosen.items():
        with Database(profile) as db:
            versions[name] = db.version()
            identities[name] = cluster_identity(db.cluster_info())
            if name in pending:
                for schema in schemas:
                    count = db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (schema,))
                    if str(count) != "0":
                        raise PCCError("TARGET_SCHEMA_EXISTS", f"{name} 已存在待导入库；请使用空的隔离环境，或人工核验后选择仅比较")
    for i, name in enumerate(names):
        if any(identities[name] & identities[other] for other in names[i + 1:]):
            raise PCCError("TARGET_NOT_ISOLATED", "集群节点有重叠，拒绝向共享后端自动导入")
    return versions


def mysql_command(profile, executable="mysql"):
    client = shutil.which(executable)
    if not client:
        raise PCCError("MYSQL_CLIENT_MISSING", "找不到 MySQL 8 客户端；请在 workflow.mysql_client 填写可执行文件路径")
    args = [client, "--no-defaults", "--protocol=TCP", "--batch", "--binary-mode", "--comments",
            "--default-character-set=utf8mb4", "--connect-timeout=5", "--local-infile=1",
            "--host=" + profile["host"], "--port=" + str(profile.get("port", 4000)), "--user=" + profile["user"]]
    tls = profile.get("tls")
    args.append("--ssl-mode=" + ("VERIFY_IDENTITY" if tls is not None else "DISABLED"))
    for key in ("ca", "cert", "key"):
        if tls and tls.get(key):
            args.append(f"--ssl-{key}=" + tls[key])
    return args


def import_target(prepared, bundle_path, target, profile, mysql_client):
    bundle = load_bundle(bundle_path)
    validate_materials(bundle)
    root = Path(prepared)
    verify(root, "Preparation")
    plan = read_json(safe_path(root, target + "/plan.json"))
    identity = {k: profile[k] for k in ("host", "port", "expected_version") if k in profile}
    if plan.get("bundle_hash") != digest(bundle.manifest) or digest(plan.get("identity")) != digest(identity):
        raise PCCError("IMPORT_MISMATCH", "导入材料与当前 bundle 或目标配置不一致")
    # The caller checks all targets together; recheck this target immediately before writing.
    with Database(profile) as db:
        db.version()
        for obj in bundle.manifest["databases"]:
            if str(db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (obj["schema"],))) != "0":
                raise PCCError("TARGET_SCHEMA_EXISTS", f"{target} 已存在待导入库，停止自动导入")
    args = mysql_command(profile, mysql_client)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR") if key in os.environ}
    reference = profile.get("password_env")
    if reference and reference not in os.environ:
        raise PCCError("CREDENTIAL_MISSING", "未设置导入密码引用")
    env.update(MYSQL_PWD=os.environ.get(reference, "") if reference else "",
               MYSQL_TEST_LOGIN_FILE=os.devnull, MYSQL_HISTFILE=os.devnull)
    script = safe_path(root, target + "/import_commands.sql").read_bytes()
    try:
        result = subprocess.run(args, input=script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    except OSError:
        raise PCCError("MYSQL_CLIENT_FAILED", "无法启动 MySQL 客户端") from None
    if result.returncode:
        # Client output can echo SQL, object names and credentials; never print it.
        match = re.search(rb"ERROR\s+(\d+)", result.stderr)
        code = match[1].decode("ascii") if match else str(result.returncode)
        raise PCCError("IMPORT_FAILED", f"{target} 导入失败，MySQL 错误码 {code}；已停止，请核验已创建对象，勿盲目重试")
