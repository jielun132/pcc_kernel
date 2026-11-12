"""Validated data-only policy overlays; proof algorithms remain in the engine."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from pcc_tool.models import PCCError, digest, primitive
from pcc_tool.plan.rule_catalog import (ASSESSMENT_VERSION, DEFAULT_RULES, DEFAULT_THRESHOLDS,
                                        REQUIRED_DATA_RULES, RULE_CATALOG)

LIMITS = {"min_rows": 1000000000, "max_ratio_pct": 50, "reduction_pct": 99, "estimate_pct": 50}
BASE_PROFILES = [
    {"id": "default", "label": "其他版本 · 继承默认规则", "old_version": "*", "new_version": "*"},
    {"id": "tidb-6.5.12-to-8.5.7", "label": "TiDB 6.5.12 → 8.5.7", "old_version": "6.5.12", "new_version": "8.5.7"},
]


def invalid(message):
    raise PCCError("RULE_CONFIG_INVALID", message)


def fields(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        invalid(name + " 含未知字段或不是对象")


def thresholds(value, base):
    fields(value, LIMITS, "thresholds")
    result = dict(base)
    for key, number in value.items():
        if (type(number) not in (str, int) or len(str(number)) > 10
                or not re.fullmatch(r"[0-9]+", str(number)) or not 1 <= int(number) <= LIMITS[key]):
            invalid("thresholds." + key + " 必须为允许范围内的整数")
        result[key] = str(int(number))
    return result


def selected(value):
    ids = {r["id"] for r in RULE_CATALOG}
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in ids for v in value) or len(set(value)) != len(value):
        invalid("selected 必须包含不重复的已知规则 ID")
    chosen = set(value) | REQUIRED_DATA_RULES
    return [r["id"] for r in RULE_CATALOG if r["id"] in chosen]


def version(value):
    return str(value).removeprefix("v")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            invalid("规则 JSON 包含重复字段")
        result[key] = value
    return result


def load_policy(path=None, profile_id=None, versions=None, archived=None):
    """Resolve exact-version defaults and serialize the original config for offline replay."""
    if path:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        except (OSError, ValueError):
            invalid("无法读取规则 JSON 配置")
    else:
        raw = copy.deepcopy(archived or {})
    fields(raw, ("schema_version", "selected", "thresholds", "cards", "profiles", "default_profile"), "规则配置")
    if type(raw.get("schema_version", 1)) not in (str, int) or str(raw.get("schema_version", 1)) != "1":
        invalid("不支持的规则配置 schema_version")
    choices = selected(raw.get("selected", DEFAULT_RULES))
    limits = thresholds(raw.get("thresholds", {}), DEFAULT_THRESHOLDS)
    cards = copy.deepcopy(RULE_CATALOG)
    patches = raw.get("cards", {})
    fields(patches, {r["id"] for r in cards}, "cards")
    for rule in cards:
        patch = patches.get(rule["id"], {})
        fields(patch, ("title", "description", "review_note"), rule["id"])
        for key, value in patch.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 8000:
                invalid(rule["id"] + "." + key + " 必须是非空文本，最多 8000 字符")
        rule.update(patch)
        rule["default"] = rule["id"] in choices
    supplied = raw.get("profiles", [])
    if not isinstance(supplied, list):
        invalid("profiles 必须是数组")
    profiles = {p["id"]: dict(p) for p in BASE_PROFILES}
    seen = set()
    for entry in supplied:
        fields(entry, ("id", "label", "old_version", "new_version", "selected", "thresholds"), "profiles")
        key = entry.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", key) or key in seen:
            invalid("规则方案 ID 无效或重复")
        seen.add(key)
        profiles[key] = {**profiles.get(key, {}), **entry}
    pairs = set()
    for key, entry in profiles.items():
        if not isinstance(entry.get("label"), str) or not entry["label"].strip() or len(entry["label"]) > 200:
            invalid("规则方案必须有 label，最多 200 字符")
        for side in ("old_version", "new_version"):
            value = entry.get(side)
            if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-.][a-zA-Z0-9.-]+)?|\*", value):
                invalid("规则方案必须指定精确版本或 *")
            entry[side] = version(value)
        pair = (entry["old_version"], entry["new_version"])
        if pair in pairs or ("*" in pair and key != "default"):
            invalid("版本组合重复；通用组合只允许 default")
        if key == "default" and pair != ("*", "*"):
            invalid("default 必须是通用版本组合")
        pairs.add(pair)
        entry["selected"] = selected(entry.get("selected", choices))
        entry["thresholds"] = thresholds(entry.get("thresholds", {}), limits)
    actual = {s: version((versions or {}).get(s, "unknown")) for s in ("old", "new")}
    chosen = profile_id or raw.get("default_profile", "auto")
    if chosen == "auto":
        chosen = next((p["id"] for p in profiles.values()
                       if (p["old_version"], p["new_version"]) == (actual["old"], actual["new"])), "default")
    if not isinstance(chosen, str) or chosen not in profiles:
        invalid("未找到指定的版本规则方案")
    active = profiles[chosen]
    for rule in cards:
        rule["default"] = rule["id"] in active["selected"]
    policy = dict(version=ASSESSMENT_VERSION, rules=cards, selected=active["selected"], thresholds=active["thresholds"],
                  profiles=list(profiles.values()), profile_id=chosen, actual_versions=actual, policy_config=primitive(raw))
    policy["rules_fingerprint"] = digest({k: v for k, v in policy.items() if k != "actual_versions"})
    return policy
