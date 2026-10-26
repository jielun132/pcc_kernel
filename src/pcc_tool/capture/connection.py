"""No credentials in request models, argv, events or driver error messages."""
from __future__ import annotations

import json
import os
import re
import ssl
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from pcc_tool.models import PCCError, primitive
from .guard import quote_identifier, validate_session

PROFILE_KEYS = {"host", "port", "user", "password_env", "status_url", "status_token_env",
                "tls", "status_tls", "expected_version", "source_cluster", "timezone", "session", "role"}
TLS_KEYS = {"ca", "cert", "key"}
CONTEXT_VARIABLES = ("sql_mode", "time_zone", "collation_connection", "character_set_connection",
                     "tidb_enable_index_merge", "tidb_isolation_read_engines", "tidb_allow_mpp", "tidb_enforce_mpp",
                     "tidb_opt_fix_control", "tidb_cost_model_version", "tidb_enable_new_only_full_group_by_check",
                     "tidb_enable_plan_cache_for_param_limit", "tidb_use_plan_baselines", "tidb_stats_load_sync_wait")


def load_profiles(path: str | None) -> dict:
    if not path:
        return {}
    try:
        value = json.loads(Path(path).read_text("utf-8"))
        profiles = value["profiles"]
        if not isinstance(profiles, dict):
            raise ValueError
        for p in profiles.values():
            validate_profile(p)
        return profiles
    except (ValueError, KeyError, OSError, TypeError):
        raise PCCError("INVALID_CONFIG", "配置文件格式无效；只允许凭据引用") from None


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict) or set(profile) - PROFILE_KEYS:
        raise PCCError("INVALID_CONFIG", "连接配置含未知字段；password/token 必须使用环境变量引用")
    if not profile.get("host") or not profile.get("user"):
        raise PCCError("INVALID_CONFIG", "连接配置必须包含 host 和 user")
    if not 1 <= int(profile.get("port", 4000)) <= 65535:
        raise PCCError("INVALID_CONFIG", "连接端口无效")
    for key in ("password_env", "status_token_env"):
        if key in profile and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", profile[key]):
            raise PCCError("INVALID_CONFIG", "凭据引用不是合法环境变量名")
    for key in ("tls", "status_tls"):
        if key in profile and (not isinstance(profile[key], dict) or set(profile[key]) - TLS_KEYS):
            raise PCCError("INVALID_CONFIG", "TLS 配置只允许 ca/cert/key 路径")
    if profile.get("status_url"):
        url = urlsplit(profile["status_url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise PCCError("INVALID_CONFIG", "Status 地址必须是无凭据的 HTTP(S) 根地址")
    validate_session(profile.get("session", {}))
    if profile.get("timezone"):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(profile["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise PCCError("INVALID_CONFIG", "配置的源时区不可用") from None
    return profile


def profile_named(profiles: dict, name: str) -> dict:
    if name not in profiles:
        raise PCCError("PROFILE_MISSING", f"未找到连接配置: {name}")
    return validate_profile(profiles[name])


def tls_context(config: dict | None):
    if config is None:
        return None
    context = ssl.create_default_context(cafile=config.get("ca"))
    if config.get("cert"):
        context.load_cert_chain(config["cert"], config.get("key"))
    return context


def clean_cell(value):
    if isinstance(value, bytes):
        # TiDB row-format plans may embed raw BINARY/VARBINARY range bytes.
        # Surrogate escapes preserve every byte and cannot collide with UTF-8 text.
        return value.decode("utf-8", "surrogateescape")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return str(Decimal(str(value)))
    return primitive(value)


class Database:
    def __init__(self, profile: dict, timeout_seconds=30):
        self.profile = validate_profile(profile)
        self.timeout = timeout_seconds
        self.connection = None

    def __enter__(self):
        import pymysql
        converters = dict(pymysql.converters.conversions)
        for field in (pymysql.constants.FIELD_TYPE.DECIMAL, pymysql.constants.FIELD_TYPE.NEWDECIMAL):
            converters[field] = lambda value: Decimal(value.decode("ascii") if isinstance(value, bytes) else value)
        password = ""
        env = self.profile.get("password_env")
        if env:
            password = os.environ.get(env)
            if password is None:
                raise PCCError("CREDENTIAL_MISSING", "未设置连接密码引用的环境变量")
        try:
            self.connection = pymysql.connect(host=self.profile["host"], port=int(self.profile.get("port", 4000)),
                user=self.profile["user"], password=password, charset="utf8mb4", use_unicode=False, autocommit=True,
                connect_timeout=5, read_timeout=max(35, self.timeout + 5), write_timeout=5,
                local_infile=False, client_flag=0, conv=converters, ssl=tls_context(self.profile.get("tls")))
        except Exception as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else "unknown"
            raise PCCError("DB_CONNECT_FAILED", f"数据库连接失败，驱动错误码 {code}") from None
        finally:
            password = None
        if self.profile.get("tls") is not None and not isinstance(self.connection._sock, ssl.SSLSocket):
            self.connection.close()
            raise PCCError("TLS_REQUIRED", "配置要求 TLS，但服务端未建立 TLS 连接")
        return self

    def __exit__(self, *_):
        if self.connection:
            self.connection.close()

    def query(self, sql: str, params=()) -> tuple[list[str], list[list]]:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, None if params == () else params)
                columns = [d[0] for d in cursor.description or []]
                rows = [[clean_cell(c) for c in r] for r in cursor.fetchall()] if cursor.description else []
                return columns, rows
        except Exception as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else "unknown"
            raise PCCError("DB_QUERY_FAILED", f"数据库语句失败，驱动错误码 {code}") from None

    def records(self, sql: str, params=()) -> list[dict]:
        columns, rows = self.query(sql, params)
        return [dict(zip(columns, row)) for row in rows]

    def scalar(self, sql: str, params=()):
        _, rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def version(self) -> str:
        version = str(self.scalar("SELECT TIDB_VERSION()"))
        match = re.search(r"Release Version:\s*(\S+)", version)
        if not match:
            raise PCCError("VERSION_UNSUPPORTED", "服务端没有返回可识别的 TiDB 精确版本")
        actual = match[1]
        expected = self.profile.get("expected_version")
        if expected and actual != expected:
            raise PCCError("VERSION_MISMATCH", "实际 TiDB 版本与声明版本不一致")
        return actual

    def variables(self, names=CONTEXT_VARIABLES) -> dict:
        result = {}
        for name in names:
            rows = self.query("SHOW SESSION VARIABLES LIKE %s", (name,))[1]
            exact = [row for row in rows if row[0].lower() == name.lower()]
            result[name] = exact[0][1] if exact else None
        return result

    def set_session(self, settings: dict):
        for key, value in validate_session(settings).items():
            self.query(f"SET SESSION {quote_identifier(key)} = %s", (value,))

    def cluster_info(self) -> list[dict]:
        return self.records("SELECT TYPE, INSTANCE, STATUS_ADDRESS, VERSION, GIT_HASH, START_TIME FROM information_schema.CLUSTER_INFO")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PCCError("STATUS_REDIRECT_REJECTED", "Status API 不允许重定向")


def status_json(profile: dict, parts: tuple[str, ...], *, raw=False):
    base = profile.get("status_url")
    if not base:
        raise PCCError("STATUS_CONFIG_MISSING", "导出统计信息需要 status_url")
    headers = {"Accept": "application/json"}
    env = profile.get("status_token_env")
    if env:
        token = os.environ.get(env)
        if not token:
            raise PCCError("CREDENTIAL_MISSING", "未设置 Status API 凭据引用")
        headers["Authorization"] = "Bearer " + token
    url = base.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in parts)
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=tls_context(profile.get("status_tls"))))
        with opener.open(Request(url, headers=headers), timeout=10) as response:
            if response.status != 200:
                raise ValueError
            body = response.read(64 * 1024 * 1024 + 1)
            if len(body) > 64 * 1024 * 1024:
                raise ValueError
            obj = json.loads(body, parse_float=Decimal)
            if not isinstance(obj, dict) or not obj:
                raise ValueError
            return body if raw else obj
    except PCCError:
        raise
    except Exception:
        raise PCCError("STATUS_API_FAILED", "Status API 返回失败、非法 JSON 或超出资源上限") from None


def verify_status_identity(db: Database, profile: dict) -> dict:
    url = urlsplit(profile.get("status_url", ""))
    if not url.hostname:
        raise PCCError("STATUS_CONFIG_MISSING", "需要 status_url")
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    address = f"{host}:{url.port or (443 if url.scheme == 'https' else 80)}"
    nodes = db.cluster_info()
    matches = [row for row in nodes if str(row["TYPE"]).lower() == "tidb" and row["STATUS_ADDRESS"] == address]
    if len(matches) != 1:
        raise PCCError("STATUS_IDENTITY_UNVERIFIED", "Status 地址不在 SQL 集群节点清单中，无法确认同源")
    status = status_json(profile, ("status",))
    def release(text):
        # /status may include a MySQL compatibility prefix; CLUSTER_INFO reports the TiDB release.
        candidate = str(text).rsplit("TiDB-", 1)[-1]
        return candidate.lstrip("v") if re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][^\s]*)?", candidate) else None
    reported, expected = release(status.get("version", "")), release(matches[0]["VERSION"])
    if not reported or reported != expected or status.get("git_hash") != matches[0].get("GIT_HASH"):
        raise PCCError("STATUS_IDENTITY_UNVERIFIED", "Status 节点版本与 SQL 集群清单不一致")
    return {"node": matches[0], "verified": True}
