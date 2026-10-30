# 固定 SQL 用例库

人工阅读：[SQL_CASES.md](SQL_CASES.md)。执行数据：[sql_cases.jsonl](sql_cases.jsonl)。

当前 413 条、17 类，覆盖单表谓词组合、连接、子查询、聚合、窗口、集合操作、数据类型、Hint、DML、Prepared 参数、跨库、视图、超长 SQL、宽树和深树，以及明确拒绝的反例。每条使用永久编号；历史行不可修改、删除或重排，后续只能追加。现有回归用例也继续保留。

## 添加用例

1. 在 `sql_cases.jsonl` 末尾增加一行，使用未用过的 `SQL-NNNN`。写明 SQL、参数、类别、覆盖目的和预期守卫结果。
2. 在 `catalog.lock.json` 末尾追加该行（包含末尾换行）的 SHA-256；不得重生成或修改历史锁定项。
3. 在 `SQL_CASES.md` 末尾追加人工说明。若补充的是旧问题，注明关联编号。
4. 运行历史完整性检查和用例校验。CI 同时对比 PR 的基线版本，无法通过修改锁文件来掩盖历史变更。

```bash
python scripts/check_case_catalog.py
python -m pytest -q tests/unit/test_case_catalog.py
```

`expected_guard=ok` 只表示程序允许将 SQL 送去 EXPLAIN，**不等于双版本均支持，也不等于计划一致**。不允许为消除失败修改旧用例的预期。

## 本地双版本验证

使用独立的本地 `prod`、`old`、`new` 三个 profile，并明确配置版本与 Status 地址。运行器只接受本机地址和全新的 `pcc_` 测试库前缀，遇到已存在的库立即退出。源端只创建模拟数据；old/new 通过生成的脚本导入结构和统计，不导入业务行。

```bash
python scripts/run_case_catalog.py prepare \
  --config /path/to/local-profiles.json \
  --schema-prefix pcc_cases_20261030_01 --out /path/to/new-run

# 分别连接 old / new，执行对应 prepared/<target>/import_commands.sql。
# 使用 mysql --local-infile=1；LOAD STATS 引用的绝对路径须在客户端所在机器可读。

python scripts/run_case_catalog.py compare \
  --config /path/to/local-profiles.json \
  --schema-prefix pcc_cases_20261030_01 --out /path/to/new-run
```

比较阶段只使用已导出并导入的同一批材料。需要再次比较时使用 `--report-name another-report`，保留上次结果，不重新覆盖统计材料。

输出包括 HTML、逐用例结果、未完成用例清单、各表前后行数和离线回放一致性证据。DML 只执行普通 EXPLAIN；行数须保持不变。可执行用例仍未完整比较时，验证脚本返回 `3` 并保留原因，不把它们计入“存在差异”。

本地验证版本为 TiDB Community v6.5.12 / v8.5.5。它验证程序行为，不替代客户 Pingkai V7.1.9 环境验收。已复现的分区统计回放缺项、TiDB 6.5.12 部分 Prepared EXPLAIN 内部错误也保留在用例库中。

## 报告交互回归

```bash
python -m pytest -q tests/unit/test_navigation.py
node tests/report_navigation.cjs /path/to/python /path/to/report.html
```

这部分需要本机 Node.js，使用轻量 DOM 验证生成脚本的分页、跳转、分组、按库暂不审核和三种剪贴板路径，不引入浏览器依赖。另以 1,053 条 SQL 检查分页与跨页操作；HTML 静态检查验证转义、链接和按页加载。它不代替浏览器中的视觉验收。
