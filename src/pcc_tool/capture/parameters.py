"""Explicit SQL PREPARE samples; values never replace question marks in SQL."""
import json
import math
import re
from datetime import date, datetime

from pcc_tool.models import PCCError


def parameter_bindings(parameters):
    """Return fixed SET expressions and driver-bound values, or reject ambiguity."""
    def invalid():
        raise PCCError("INVALID_PARAMETERS", "参数必须提供受支持的明确类型和值，且处于该类型范围内")

    if not isinstance(parameters, list) or len(parameters) > 65535:
        invalid()
    result = []
    for parameter in parameters:
        if not isinstance(parameter, dict) or set(parameter) != {"type", "value"}:
            invalid()
        kind, value = parameter["type"], parameter["value"]
        if not isinstance(kind, str):
            invalid()
        expression = "%s"
        if kind == "null":
            if value is not None:
                invalid()
        elif kind == "bool":
            if not isinstance(value, bool):
                invalid()
            value = int(value)
        elif kind in {"int", "uint"}:
            if not isinstance(value, str) or not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,19})", value):
                invalid()
            lower, upper = (0, 2**64 - 1) if kind == "uint" else (-2**63, 2**63 - 1)
            if not lower <= int(value) <= upper:
                invalid()
            expression = "CAST(%s AS UNSIGNED)" if kind == "uint" else "CAST(%s AS SIGNED)"
        elif kind == "decimal":
            if not isinstance(value, str) or not re.fullmatch(r"-?[0-9]{1,65}(?:\.[0-9]{1,30})?", value):
                invalid()
            integer, _, fraction = value.lstrip("-").partition(".")
            precision = max(1, len(integer.lstrip("0")) + len(fraction))
            if precision > 65:
                invalid()
            expression = f"CAST(%s AS DECIMAL({precision},{len(fraction)}))"
        elif kind == "double":
            if not isinstance(value, str) or not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
                invalid()
            value = float(value)
            if not math.isfinite(value):
                invalid()
        elif kind == "string":
            if not isinstance(value, str):
                invalid()
        elif kind == "bytes":
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", value):
                invalid()
            expression = "UNHEX(%s)"
        elif kind in {"date", "datetime"}:
            pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
            if kind == "datetime":
                pattern += r" [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?"
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                invalid()
            try:
                (date if kind == "date" else datetime).fromisoformat(value)
            except ValueError:
                invalid()
            expression = "CAST(%s AS DATE)" if kind == "date" else "CAST(%s AS DATETIME(6))"
        elif kind == "time":
            match = re.fullmatch(r"-?([0-9]{1,3}):([0-9]{2}):([0-9]{2})(?:\.[0-9]{1,6})?", value) if isinstance(value, str) else None
            if not match or int(match[1]) > 838 or int(match[2]) > 59 or int(match[3]) > 59:
                invalid()
            expression = "CAST(%s AS TIME(6))"
        elif kind == "json":
            if not isinstance(value, str):
                invalid()
            try:
                json.loads(value, parse_constant=lambda _: invalid())
            except (ValueError, RecursionError):
                invalid()
            expression = "CAST(%s AS JSON)"
        else:
            invalid()
        result.append((expression, value))
    return result


def prepare_explain(db, sql, parameters, format, check_cancelled=lambda: None):
    """Prepare EXPLAIN itself: EXECUTE can never run the underlying DML."""
    bindings = parameter_bindings(parameters)
    check_cancelled()
    db.query("PREPARE pcc_explain FROM %s", (f"EXPLAIN FORMAT='{format}' " + sql,))
    warnings = db.records("SHOW WARNINGS")
    names = []
    for index, (expression, value) in enumerate(bindings):
        check_cancelled()
        name = f"@pcc_arg_{index}"
        db.query(f"SET {name} = {expression}", (value,))
        if db.records("SHOW WARNINGS"):
            raise PCCError("PARAMETER_COERCION", "参数绑定产生警告，不能保证参数样例类型和值完整")
        names.append(name)
    return "EXECUTE pcc_explain" + (" USING " + ",".join(names) if names else ""), warnings
