import json
import re
from pathlib import Path

import pytest

from pcc_tool.models import PCCError, primitive
from pcc_tool.plan.rule_catalog import DEFAULT_RULES, REQUIRED_DATA_RULES
from pcc_tool.plan.rule_config import load_policy
from pcc_tool.render.assessment import assessment_markup


def test_shipped_rule_example_loads_and_overrides_builtin_version_profile():
    policy = load_policy(Path(__file__).parents[2] / 'examples/rules.example.json',
                         versions={'old': 'v6.5.12', 'new': 'v8.5.7'})
    assert policy['profile_id'] == 'tidb-6.5.12-to-8.5.7'
    assert policy['selected'] == DEFAULT_RULES
    assert next(r for r in policy['rules'] if r['id'] == 'A01')['review_note'].startswith('人工复核')
    assert load_policy(archived=policy['policy_config'], versions=policy['actual_versions']) == policy


def test_exact_pair_auto_selection_and_custom_fallback(tmp_path):
    known = load_policy(versions={"old": "v6.5.12", "new": "v8.5.7"})
    assert known["profile_id"] == "tidb-6.5.12-to-8.5.7" and known["selected"] == DEFAULT_RULES
    fallback = load_policy(versions={"old": "v6.5.12", "new": "v8.5.7-custom"})
    assert fallback["profile_id"] == "default"
    path = tmp_path / "rules.json"
    raw = {"schema_version": 1, "selected": ["E01"], "thresholds": {"min_rows": 4321},
           "cards": {"E01": {"title": "客户评审", "review_note": "</script><img onerror=alert(1)>", "description": "自定义说明"}},
           "profiles": [{"id": "custom", "label": "客户版本", "old_version": "6.5.12", "new_version": "8.5.7-custom",
                         "selected": ["A01"], "thresholds": {"estimate_pct": 7}}]}
    path.write_text(json.dumps(raw))
    policy = load_policy(path, versions={"old": "v6.5.12", "new": "v8.5.7-custom"})
    assert policy["profile_id"] == "custom" and set(policy["selected"]) == REQUIRED_DATA_RULES | {"A01"}
    assert policy["thresholds"]["min_rows"] == "4321" and policy["thresholds"]["estimate_pct"] == "7"
    assert load_policy(archived=policy["policy_config"], profile_id="custom", versions=policy["actual_versions"]) == policy
    markup = assessment_markup("report", policy)
    assert 'id="threshold-min_rows" value="4321"' in markup
    assert '<img onerror=' not in markup and '&lt;img' in markup
    embedded = json.loads(re.search(r'id="assessment-config">(.*?)</script>', markup, re.S)[1])
    assert embedded["policy_config"] == primitive(raw)


@pytest.mark.parametrize("raw", [
    [], {"schema_version": True}, {"schema_version": 1.0}, {"schema_version": 2}, {"typo": 1},
    {"selected": ["BAD"]}, {"selected": ["E01", "E01"]}, {"selected": "E01"},
    {"thresholds": {"min_rows": True}}, {"thresholds": {"max_ratio_pct": 51}},
    {"thresholds": {"min_rows": "1e9"}}, {"thresholds": {"unknown": 1}},
    {"cards": {"E01": {"automatic": True}}}, {"cards": {"D01": {"default": False}}},
    {"cards": {"UNKNOWN": {"title": "unknown"}}}, {"profiles": [{}]},
    {"profiles": [{"id": "bad", "label": "bad", "old_version": "*", "new_version": "8.5.7"}]},
    {"default_profile": "missing"}, {"default_profile": []},
])
def test_invalid_or_proof_overriding_config_fails_closed(tmp_path, raw):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(PCCError) as error:
        load_policy(path)
    assert error.value.code == "RULE_CONFIG_INVALID"


def test_rule_fingerprint_changes_with_notes_thresholds_and_selection():
    base = load_policy()
    for change in ({"cards": {"E01": {"review_note": "确认列来源"}}}, {"thresholds": {"min_rows": 2000}}, {"selected": []}):
        policy = load_policy(archived=change)
        assert base["rules_fingerprint"] != policy["rules_fingerprint"]
        assert REQUIRED_DATA_RULES <= set(policy["selected"])


def test_duplicate_json_fields_are_not_silently_overridden(tmp_path):
    path=tmp_path/'duplicate.json'
    path.write_text('{"thresholds":{"min_rows":1000,"min_rows":1}}')
    with pytest.raises(PCCError) as error:
        load_policy(path)
    assert error.value.code=='RULE_CONFIG_INVALID'
