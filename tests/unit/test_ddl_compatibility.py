import pytest

from pcc_tool.collector.dependencies import ddl_semantic_signature as signature


@pytest.mark.parametrize("kind,width", [("tinyint",4),("smallint",6),("mediumint",9),("int",11),("bigint",20)])
def test_integer_display_width_does_not_block_cross_version_import(kind, width):
    before = f"CREATE TABLE `t` (`a` {kind}({width}) unsigned NOT NULL, `b` int(11) DEFAULT NULL)"
    after = f"CREATE TABLE `t` (`a` {kind} unsigned NOT NULL, `b` int DEFAULT NULL)"
    assert signature(before) == signature(after)


@pytest.mark.parametrize("before,after", [
    ("`a` int unsigned", "`a` int"),
    ("`a` int", "`a` bigint"),
    ("`a` decimal(20,6)", "`a` decimal(20,5)"),
    ("`a` bit(8)", "`a` bit(1)"),
    ("`a` varchar(20)", "`a` varchar(30)"),
    ("`a` int(10) zerofill", "`a` int(11) zerofill"),
    ("`a` varchar(40) DEFAULT 'int(11)'", "`a` varchar(40) DEFAULT 'int'"),
    ("`a` int COMMENT 'bigint(20)'", "`a` int COMMENT 'bigint'"),
    ("`int(11)` int", "`int` int"),
    ("`a` int /*T![test] int(11) */", "`a` int /*T![test] int */"),
    ("`a` int, KEY `k` (`a`)", "`a` int"),
])
def test_real_schema_changes_are_preserved(before, after):
    assert signature(f"CREATE TABLE `t` ({before})") != signature(f"CREATE TABLE `t` ({after})")
