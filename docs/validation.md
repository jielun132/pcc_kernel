# 验证结果与已知问题

## 实测范围与结论

本页是 **2026-10-15、PCC 0.1.2** 现场测试的公开摘要。原始连接配置、服务器地址、完整日志与现场报告由维护者保留，不随开源仓库发布。源码中的集成测试和合成夹具可用于在获得授权的隔离环境复测。

**尚未达到“三版本、所有 SQL、完整统计导入全部通过”。** 最终在线检查1,260项：1,233通过、27失败、0错误、0跳过；368项离线单元测试通过。这里统计 pytest 检查项，不是独立 SQL 数量或生产覆盖率。已知失败保留严格断言，没有改成 xfail 或删除。

| 实际版本 | 逐版本检查 | 通过 | 失败 |
|---|---|---|---|
| TiDB Enterprise 6.5.3 | 413 | 404 | 9 |
| TiDB Enterprise 6.5.12 | 413 | 404 | 9 |
| Enterprise Release 7.1.9-0.3 / Core 8.5.5 | 413 | 410 | 3 |
| 六方向跨版本及持久化 | 21 | 15 | 6 |
| 合计 | 1260 | 1233 | 27 |

新内核现场返回 **Core 8.5.5**；此结果不代表社区版7.1.9、全部8.5补丁或8.5.8已通过认证。每套环境均为3个 TiDB、3个 PD、3个 TiKV，没有 TiFlash。测试仅使用专用库 `pcc_validation_20261011` 的合成数据。

## 功能结果

| 功能 | 三套环境结果与边界 |
|---|---|
| 表结构、嵌套视图依赖、普通表及分区统计导出 | 已测通过，保存原 DDL 和统计字节 |
| 指定时间窗的 SQL、指标及上下文导出 | 已测通过；3+5+7次查询跨节点汇总为15 |
| 表结构导入 | 源环境生成夹具；PCC 生成的 DDL 实际导入另两套空测试库；六方向已有对象核验通过 |
| 普通表统计导入 | 六方向同源回放及内容指纹检查通过 |
| 分区统计完整导入 | 未通过，见F1 |
| SQL 材料包重读与后续消费 | 已测通过；不向 TiDB 内部 Summary 表写回历史记录 |
| 不指定窗口的持续采集 | watch两批→SIGINT→新进程再采一批，保留文件且不重复累计 |
| 在线与离线结果一致 | 六方向 full/common 共12组结果指纹一致，含失败结果 |
| 普通 SQL、Hint、长 SQL 比较 | 已测集合通过；不代表所有语法组合 |
| Prepared SQL | 部分通过，见F2、F3 |
| 原生 Summary 持久化开启模式 | 未测；本轮验证的是 PCC 文件持久化 |

导入由测试脚本显式执行，PCC `prepare` 本身仍只生成材料。时间窗采用相交的原始 Summary 窗口累计值，统计为导出时的当前统计，不冒充历史事务快照。

跨版本控制集113条：6.5.3↔6.5.12为112 same / 1 changed；两套6.5↔新内核均为93 same / 20 changed。changed 是正常比较结果，不代表测试失败，也不能直接解释为性能变化。

另有完整115条集合，保留 `partition_explicit` 和 `long_prepared_256`，每个方向均有2条不可完整比较。控制集明确排除这两条用于正向验证，不能代替完整验收。

## 已知失败

### F1：分区统计回放缺少 FM Sketch

三套版本均复现：源分区表统计包含6个 FM Sketch，原生 LOAD STATS 客户端退出0后重新导出为0个。PCC 判定 STATS_NOT_READY，不能计为完整比较。三次同版本回放与六方向回放严格断言仍失败。

保留原始统计 JSON，不删除 FM Sketch、不改写内容，也不通过 ANALYZE 目标空表绕过核验。需要数据库侧修复或明确导入限制后再验收。

### F2：6.5 的 Prepared EXPLAIN IN 路径

6.5.3 / 6.5.12 准备 `EXPLAIN FORMAT='row' SELECT id FROM orders WHERE id IN (?)` 时返回1105内部错误；1、2、8、256个占位符均复现，关闭测试会话的 prepared plan cache 无效。新内核通过。

直接准备并执行相应只读 SELECT 在三版本均通过，因此这是 **PCC 当前 Prepared EXPLAIN 采集路径的限制**，不能表述为业务 prepared IN 语法不受 TiDB 支持。没有拼接参数常量、改写原 SQL 或执行待测 DML 来掩盖问题。

### F3：负 TIME / JSON 的二进制参数对照

独立对照使用 mysql-connector-python 8.4.0 pure 模式：负 TIME 的 Python timedelta 二进制编码与 PCC SQL TIME 路径在服务端回读值不同；JSON 对照传字符串，而 PCC `json` 参数转换为 SQL JSON，类型与文本表示不同。三版本各有两条严格对照断言失败。

新增的负 TIME 字符串、JSON 字符串和正 TIME 二进制对照在三版本通过。应用发送文本时应提供 `string` 参数，不能靠内容猜类型。不能将本次差异推广为全部应用驱动存在同一缺陷。

## SQL 与类型覆盖

原基础27条、主矩阵115条、新增56条 SQL/参数/表结构场景，以及16类明确拒绝的命令。类别有重叠，不能直接相加为业务覆盖率。

- 主矩阵：39条语法、20条字段类型、25条 Hint、24条 Prepared、7条长 SQL；双节点、row / tidb_json 两种格式。
- 数值、DECIMAL(65,30)、BIT/BOOL、字符与二进制、各 TEXT/BLOB、日期时间、ENUM/SET/JSON、NULL、存储及虚拟生成列。
- 参数覆盖整数边界、空值、精度、闰日、微秒、负/超24小时TIME、中文、emoji、问号/引号/分号、LIKE、LIMIT/OFFSET。
- 长 SQL 达1,048,654字节；20,000项 IN；128分支 UNION ALL；256参数；114,688字节参数及 COM_STMT_SEND_LONG_DATA。
- 表布局包括复合聚簇主键、非聚簇主键、无主键、前缀/不可见索引、HASH、RANGE、RANGE COLUMNS 分区与嵌套视图。
- 未知/无效 Hint 保留警告；对应测试通过不代表 Hint 生效。DDL/DCL/TCL/管理命令的拒绝测试不代表支持这些命令的计划比较。

尚未认证：真实8.5.8、TiFlash/MPP、向量检索、全部全局/表达式索引组合、LIST分区、临时表/Sequence/Placement/外键依赖、原生Summary持久化开启模式、旧repository实库、全部字符集/SQL mode/时区组合、Prepared Plan Cache、全部JDBC/Go等驱动、故障切换及长时间压力。

## 本轮修复与离线交付

0.1.2 修复了整数显示宽度的结构签名、Summary 截断 UTF-8、INSERT/REPLACE SET、UPDATE IGNORE、OrderedWindow 和失败结果重复问题造成的在线/离线指纹不一致。契约版本保持1；normalizer=2、matcher=1、diff=2、guard=3、materials=1。旧包要使用跨版本显示宽度兼容时应重新导出。

CentOS 8 / x86_64 / glibc 2.28 原生包验证：无外部 Python 的 doctor、113条控制集在线比较、115条含失败集合、两组无凭据离线重算；strace 未发现离线重算的 AF_INET/AF_INET6 调用。189个包文件哈希一致，新建虚拟环境从本地 wheelhouse 离线安装 SDK 并重算一致。这不是所有 Linux 环境或全部功能的认证。

现场历史二进制和原始报告不作为公开 Release 附件；从公开源码构建的方法见 [distribution.md](distribution.md)。

## 复测入口

离线检查：

```bash
python -m pytest -q tests/unit
```

默认 `pytest -q` 会跳过未显式启用的在线测试；跳过不代表兼容性通过。真实环境测试需先授权、建立合成夹具、按实际版本配置 SQL/Status 及密码环境变量引用，并为每套测试选择新的输出目录：

| 套件 | 启用变量 | 文件 |
|---|---|---|
| SQL 矩阵 | PCC_RUN_SQL_MATRIX=1 | tests/integration/test_sql_matrix.py |
| Prepared 二进制对照 | PCC_RUN_PREPARED_PROTOCOL=1 | tests/integration/test_prepared_protocol.py |
| 单集群回归 | PCC_RUN_SINGLE_CLUSTER=1 | tests/integration/test_single_cluster.py |
| 额外语法/类型/布局 | PCC_RUN_SCENARIOS=1 | tests/integration/test_scenario_matrix.py |
| 三集群与六方向 | PCC_RUN_VERSION_WORKFLOWS=1 | tests/integration/test_version_workflows.py |

前四项设置 `PCC_TEST_CONFIG` 和 `PCC_LIVE_OUTPUT`。三集群套件使用 `PCC_VERSION_CONFIG`，其中 profile 名固定为 `v653`、`v6512`、`enterprise`，每个 profile 均须 `role=test` 和精确 `expected_version`。另设置 `PCC_TEST_INSTANCE_HOSTS`，JSON 中每个集群提供三个不同、经授权的 TiDB 主机；各主机使用该 profile 的同一个 SQL 端口。例如：

```bash
export PCC_TEST_INSTANCE_HOSTS='{"v653":["a.example.invalid","b.example.invalid","c.example.invalid"],"v6512":["d.example.invalid","e.example.invalid","f.example.invalid"],"enterprise":["g.example.invalid","h.example.invalid","i.example.invalid"]}'
```

这些是不可连接的占位地址，必须按自己的隔离环境填写。测试会在固定专用测试库内进行夹具写入、ANALYZE 和 LOAD STATS；部分导入测试还需要 MySQL 客户端。二进制对照单独安装 `tests/requirements-protocol.txt`，不属于 PCC 运行依赖。目前完整在线套件预期仍能复现上述失败，不应只选择正向控制集宣布验收完成。
