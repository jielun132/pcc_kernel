"""Parse to validate; always send the original SQL bytes to EXPLAIN."""
import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot.dialects.mysql import MySQL
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.tokens import TokenType

from pcc_tool.models import PCCError

logging.getLogger("sqlglot").addHandler(logging.NullHandler())
# The parser otherwise logs complete SQL for Command fallbacks.
logging.getLogger("sqlglot").propagate = False

DENIED_FUNCTIONS = {"SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS",
                    "LOAD_FILE", "NEXTVAL", "SETVAL", "NEXT_VALUE_FOR"}
SYSTEM_SCHEMAS = {"mysql", "information_schema", "performance_schema", "metrics_schema", "sys"}


class TiDBValidation(MySQL):
    class Parser(MySQL.Parser):
        def _parse_update(self):
            ignore = self._match(TokenType.IGNORE)
            tree = super()._parse_update()
            tree.meta["tidb_ignore"] = ignore
            return tree

        def _parse_insert(self):
            tree = super()._parse_insert()
            if isinstance(tree, exp.Insert) and tree.expression is None and self._match(TokenType.SET):
                assignments = self._parse_csv(self._parse_equality)
                if not assignments or any(not isinstance(a, exp.EQ) or not isinstance(a.this, exp.Column)
                                          or a.this.table for a in assignments):
                    self.raise_error("Expected column assignments after INSERT SET")
                tree.set("this", exp.Schema(this=tree.this, expressions=[a.this.this for a in assignments]))
                tree.set("expression", exp.Values(expressions=[exp.Tuple(expressions=[a.expression for a in assignments])]))
                tree.set("conflict", self._parse_on_conflict())
            return tree

        def _parse_delete(self):
            tree = super()._parse_delete()
            order = self._parse_order()
            if order is not None:
                tree.set("order", order)
                tree.set("limit", self._parse_limit())
            return tree


def inspect_lexical_sql(sql: str):
    """SQLGlot intentionally ignores executable comments; reject them before parsing."""
    hints = []
    i = 0
    while i < len(sql):
        c = sql[i]
        if c in "'\"`":
            quote, i = c, i + 1
            while i < len(sql):
                if sql[i] == "\\":
                    i += 2
                elif sql[i] == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            else:
                raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "SQL 字符串或标识符被截断")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "SQL 注释被截断")
            comment = sql[i+2:end].lstrip()
            if comment.startswith("!") or comment.upper().startswith("T!"):
                raise PCCError("SQL_UNSUPPORTED", "不支持可执行版本注释")
            if "/*" in comment:
                raise PCCError("SQL_UNSUPPORTED", "不支持嵌套注释")
            if comment.startswith("+") and re.search(r"\bSET_VAR\s*\(", comment, re.I):
                raise PCCError("SQL_REJECTED", "SET_VAR Hint 不能绕过会话变量白名单")
            if comment.startswith("+"):
                hints.append((i, end + 2))
            i = end + 2
        elif c == "#" or (sql.startswith("--", i) and (i+2 == len(sql) or sql[i+2].isspace())):
            end = sql.find("\n", i)
            i = len(sql) if end < 0 else end + 1
        else:
            i += 1
    # TiDB has hints SQLGlot does not understand, notably after UPDATE/DELETE.
    # Validate a view with optimizer comments masked; never rewrite captured SQL.
    parts, start = [], 0
    for begin, end in hints:
        parts.extend((sql[start:begin], re.sub(r"[^\r\n]", " ", sql[begin:end])))
        start = end
    return "".join(parts) + sql[start:]


def guard_sql(sql: str | None, allowed=("select", "update", "delete"), *, parameters=None) -> tuple[exp.Expression, str]:
    if not sql or not sql.strip():
        raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "缺少可执行 SQL 样例")
    if "\x00" in sql:
        raise PCCError("SQL_UNSUPPORTED", "不支持可执行版本注释或 NUL 字符")
    validation_sql = inspect_lexical_sql(sql)
    if re.match(r"\s*(?:EXECUTE|PREPARE)\b", sql, re.I):
        raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "Prepared SQL 需要完整参数样例")
    try:
        tokens = sqlglot.tokenize(validation_sql, read="mysql")
        if tokens and tokens[0].text.upper() in {"PREPARE", "EXECUTE"}:
            raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "Prepared SQL 需要 SQL 模板和完整参数样例")
        replace = bool(tokens and tokens[0].text.upper() == "REPLACE")
        if replace:
            token = tokens[0]
            validation_sql = validation_sql[:token.start] + "INSERT" + validation_sql[token.end + 1:]
        statements = sqlglot.parse(validation_sql, read=TiDBValidation, error_level=ErrorLevel.RAISE)
    except (SqlglotError, RecursionError):
        raise PCCError("SQL_UNSUPPORTED", "SQL 解析失败，可能已截断或语法尚不支持") from None
    statements = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
    if len(statements) != 1:
        raise PCCError("SQL_REJECTED", "只允许完整的单条 SQL")
    tree = statements[0]
    kind = "select" if isinstance(tree, (exp.Select, exp.SetOperation)) else tree.key
    if replace and isinstance(tree, exp.Insert):
        kind = "replace"
    if kind not in {"select", "update", "delete", "insert", "replace"} or kind not in allowed:
        raise PCCError("SQL_REJECTED", "语句类型不在允许列表中")
    placeholders = 0
    for node in tree.walk():
        if isinstance(node, exp.Placeholder) and parameters is not None and not node.this:
            placeholders += 1
            continue
        if isinstance(node, (exp.Placeholder, exp.Parameter, exp.SessionParameter)):
            raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "未绑定参数或会话变量需要补充样例")
        if isinstance(node, (exp.Command, exp.Into, exp.Lock, exp.PropertyEQ)):
            raise PCCError("SQL_REJECTED", "不允许未知命令、输出文件、锁或变量赋值")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if name.upper() in DENIED_FUNCTIONS:
                raise PCCError("SQL_REJECTED", "SQL 包含有副作用或阻塞的函数")
    if parameters is not None:
        from pcc_tool.capture.parameters import parameter_bindings
        parameter_bindings(parameters)
        if placeholders != len(parameters):
            raise PCCError("NEEDS_EXECUTABLE_SAMPLE", "占位符数量与明确提供的参数数量不一致")
    return tree, kind


def dependencies(tree: exp.Expression, default_schema: str) -> list[list[str]]:
    # Scope resolution distinguishes a physical table from a CTE of the same name.
    tables, seen = [], set()
    for scope in traverse_scope(tree):
        for node, source in scope.selected_sources.values():
            seen.add(id(node))
            if isinstance(source, exp.Table):
                tables.append(source)
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
        aliases = {t.alias for t in tree.find_all(exp.Table) if t.alias}
        for table in tree.find_all(exp.Table):
            if id(table) not in seen and (table.db or table.name not in ctes | aliases):
                tables.append(table)
    result = set()
    for table in tables:
        if table.catalog or not isinstance(table.this, exp.Identifier):
            raise PCCError("DEPENDENCY_UNSUPPORTED", "跨 catalog 或动态表依赖不支持")
        schema = table.db or default_schema
        if not schema:
            raise PCCError("DEPENDENCY_UNRESOLVED", "未限定表名需要默认 Schema")
        if schema.lower() in SYSTEM_SCHEMAS:
            raise PCCError("DEPENDENCY_UNSUPPORTED", "系统表不作为复现业务依赖")
        result.add((schema, table.name))
    return [list(t) for t in sorted(result)]


def quote_identifier(value: str) -> str:
    if not value or "\x00" in value:
        raise PCCError("INVALID_IDENTIFIER", "对象名称为空或包含 NUL")
    return "`" + value.replace("`", "``") + "`"


def validate_session(values: dict) -> dict:
    boolean = {"tidb_enable_index_merge", "tidb_allow_mpp", "tidb_enforce_mpp",
               "tidb_enable_chunk_rpc", "tidb_enable_cascades_planner"}
    integers = {"tidb_stats_load_sync_wait": (0, 30000), "tidb_cost_model_version": (1, 2)}
    result = {}
    for key, value in values.items():
        if key in boolean:
            if str(value).lower() not in {"0", "1", "on", "off", "true", "false"}:
                raise PCCError("INVALID_SESSION", "布尔会话变量值无效")
            result[key] = 1 if str(value).lower() in {"1", "on", "true"} else 0
        elif key == "tidb_isolation_read_engines":
            engines = str(value).split(",")
            if not engines or any(e not in {"tidb", "tikv", "tiflash"} for e in engines):
                raise PCCError("INVALID_SESSION", "读取引擎值无效")
            result[key] = ",".join(engines)
        elif key in integers:
            try:
                if not re.fullmatch(r"[0-9]+", str(value)):
                    raise ValueError
                number = int(value)
            except (TypeError, ValueError):
                raise PCCError("INVALID_SESSION", "会话变量必须为整数") from None
            if not integers[key][0] <= number <= integers[key][1]:
                raise PCCError("INVALID_SESSION", "会话变量超出范围")
            result[key] = number
        elif key == "time_zone" and re.fullmatch(r"[+-](?:0\d|1[0-3]):[0-5]\d", str(value)):
            result[key] = value
        elif key == "sql_mode" and all(v in {"", "STRICT_TRANS_TABLES", "STRICT_ALL_TABLES", "NO_ZERO_DATE",
                  "NO_ZERO_IN_DATE", "ERROR_FOR_DIVISION_BY_ZERO", "NO_ENGINE_SUBSTITUTION", "ONLY_FULL_GROUP_BY"}
                  for v in str(value).split(",")):
            result[key] = value
        else:
            raise PCCError("INVALID_SESSION", "会话变量不在经过验证的白名单中")
    return result
