# 计划夹具来源

`test_plan.py::test_json_and_row_same` 使用 TiDB 官方 EXPLAIN 文档中 Projection → IndexReader → IndexRangeScan 的 JSON 结构和算子属性。官方字段缺省表示空值，JSON 子节点字段为 `subOperators`。

- 首选中文文档：https://pingkai.cn/docs/tidb/dev/sql-statement-explain
- 版本文档补充：https://docs.pingcap.com/tidb/v6.5/sql-statement-explain/

本次检索中 pingkai.cn 的具体版本页面不可读，结构细节由官方 docs.pingcap.com 的 v6.5 页面补充。

其他计划是明确标注用途的人工边界夹具，不能替代真实集群计划。Golden 测试同时断言应触发事件和不得触发事件。SELECT/WITH/UNION/子查询、DML 的 SQL 守卫测试不代表对应计划已经经过真实环境认证。

用户提供隔离测试环境后，需要补录带精确补丁版本与 SQL/DDL/stats 来源的脱敏计划，扩充 CTE/视图/分区/IndexMerge/Apply/自连接/TiFlash/MPP 等真实 Golden 证据。不得把这些人工夹具标成真实采集。
# 真实计划回归夹具

`live-plans-v719-03.json` 来自 2026-10-11 在隔离合成测试集群上的普通 EXPLAIN，精确 Release 为 `v7.1.9-0.3`，Enterprise Core 为 `v8.5.5`。SQL 仅引用 `live_setup.sql` 创建的专用测试数据；文件保留 9 类查询的 row/JSON 原始计划，共 18 份，不含凭据。它验证该实际构建的解析形状，不能用于宣称所有社区版 7.1.9 行为相同。

# 2026-10-15 supplementary fixture

`live-ordered-window.json` preserves row and tidb_json snapshots for a CTE with
ROW_NUMBER from v7.1.9-0.3 / Core v8.5.5. The regression verifies OrderedWindow
without equating it to Window or erasing partition/order/frame expressions.


公开版本仅替换 `live-ordered-window.json` 中的端点主机名为 `fixture-node.example.invalid`；SQL、row/JSON 计划和算子属性保留不变。
