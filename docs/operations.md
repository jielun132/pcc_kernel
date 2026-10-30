# 操作约定与支持边界

生产连接只执行源元数据、Statement Summary、SHOW 和 Status API 读取；`collect/export` 不写生产数据库。`compare` 仅接受 `role=test` 的两个不同 SQL 端点，worker 独占连接，执行同连接 USE、白名单 SESSION 设置、普通 EXPLAIN 和紧随的 SHOW WARNINGS。带明确 parameters 的样例使用 SQL PREPARE 准备 EXPLAIN 本身、固定名称的用户变量绑定和 EXECUTE；连接关闭释放 Prepared Statement，业务 DML 不会执行。测试目标应直连固定 TiDB 节点，不应使用无法固定后端的负载均衡地址。默认每端最多并发 2、连接超时 5 秒、每次 EXPLAIN 30 秒、读取超时至少 35 秒。

权限按实际环境验证：源端读取 Summary、CLUSTER_INFO、SHOW CREATE、SHOW STATS、SHOW GLOBAL BINDINGS；Status API 读取 `/status`、`/stats/dump/<db>/<table>`；目标端读取材料及执行普通 EXPLAIN、允许的 SESSION SET；取消连接需要可终止本任务查询并读取 CLUSTER_PROCESSLIST 的权限。能力不可见会记录失败或覆盖限制，不自行提高权限或修改全局配置。

会话白名单目前包括有限 SQL mode、固定偏移 time_zone、MPP 开关、索引合并开关、读取引擎、统计同步加载等待等。ANSI_QUOTES / NO_BACKSLASH_ESCAPES 等影响解析词法的模式尚未支持。不接受任意 SET，拒绝 SET_VAR Hint。Hint、字面量与原始 SQL 字节保留；普通 EXPLAIN 会重新优化，不复现 Prepared Plan Cache 命中。

SQL 语义、字符集/排序规则、时区、引擎/MPP/Binding，以及显式声明的会话变量不同，会阻止完整比较。其他版本自带的优化器默认值差异会保留为警告，允许报告可比较的计划事实；不把这种差异直接解释成版本性能回退。可以显式设置白名单中的 `tidb_cost_model_version`（1 或 2）锁定测试条件，但必须以目标版本实际支持能力为准。

绑定默认 `exclude`，只设置目标连接的 `tidb_use_plan_baselines=0`；不会删除或下发 Binding。显式 `preserve` 会记录并核对目标 Binding 与来源材料，不一致时标为覆盖不足。源历史 Session 未知与当前采集 Session 分别记录。

材料包含 SHOW CREATE DATABASE/TABLE/VIEW，视图按依赖递归排列；保留原始统计 JSON 字节与 hash。Sequence、Placement Policy、外键、临时表及不能解析的视图依赖明确报错。TiFlash/MPP、数据相关子查询的生产复现仍需集群能力或数据条件；仅 schema+stats 不构成完整生产快照。

`prepare` 只生成可审核的 SQL 和核验清单。已有对象必须先比对结构，人工跳过相应 CREATE；没有自动 DROP/TRUNCATE/ANALYZE 或 `--apply`。LOAD STATS 的绝对路径、MySQL 客户端 local infile 传输和精确补丁行为必须由隔离环境验证。PCC 自身连接始终关闭 local_infile 和多语句能力。

`--targets old,new` 为两个配置名生成各自的 `import_commands.sql`、`verification.sql` 和 `plan.json`，结构与统计原文件共用 `prepared/materials/`。当前两个目标的导入 SQL 内容相同，不根据版本转换格式；该参数不表示向两个集群实际导入。时间范围和五维 Top N 在 `export` 选择，`prepare` 不再筛 SQL。

导入脚本包含建库、建表及 `LOAD STATS`。独立且没有同名库的两个目标，首次执行各自脚本时才会建库；无需提前手工建库。若报“数据库已存在”，先记录完整报错行号、实际脚本和目标 DDL 历史，再核对连接及环境状态；仅凭步骤文档或后续报告不能确定是谁、何时创建了库。不要直接加 `IF NOT EXISTS` 或 `mysql --force` 将错误跳过并当成导入完整成功。

示例中的 `sleep 5` 只给统计加载留出时间，不是就绪校验；`compare --stats-retries 5` 仍会按现有逻辑检查并重试。空目标表不能通过重新 `ANALYZE` 来代替导入源统计。当前 `stats:partial[...]` 会作为计划属性差异保留，不能把等待或成功获得 EXPLAIN 当成所有统计已经完整加载的证明。

compare 前后核对对象 DDL、表/分区行数、列/索引统计明细和连接上下文；已返回的计划含 pseudo 或统计加载超时仍判 STATS_NOT_READY，保留有限重试的所有原始结果。自动 Analyze、外部写入等由 DBA 控制，PCC 只核验。

建议源端和两个目标都配置各自的 `status_url`。通过 SQL 集群节点清单与 `/status` 的版本、Git hash 验证同源后，PCC 比较持久化统计内容，避免把节点尚未加载的直方图误判为不同统计。原始 JSON 保持不变；语义指纹仅排除采集/使用时间元数据、TSO 更新版本，并规范化 FM Sketch 哈希集合顺序，保留桶、TopN、sketch 值、行数、NDV、大小、相关性与统计版本。未配置目标 Status API 时继续保守核对 `SHOW STATS`；节点惰性加载导致明细不全会返回 `STATS_NOT_READY`，不会假定相同。通过白名单 `tidb_stats_load_sync_wait` 可指定本次 EXPLAIN 的统计同步加载等待时间。

旧 19 列 TSV 与只读 repository 不具备键扫描指标。旧 metadata/stats 文件可随补充 `queries.jsonl` 的旧包导入，严格核对对象名，绝不执行 shell 或 `top_sql_selection.sql`。旧包没有源直方图核验基线时保留材料并标 partial，需要重新从源端导出后才能计入完整比较。

产物含业务 SQL、schema 和统计上下文，应按用户数据管理。文件默认为 0600，输出工作目录为 0700；不包含连接密码/令牌。报告中的原文全部 HTML 转义，CSV 对危险前缀进行防公式处理。ZIP 解包函数只接受受限相对路径、普通文件和目录，拒绝符号链接及越界；CLI 目前使用目录材料包。

退出码 3 优先于发现差异的 1。缺样例、失败、未知语义、匹配歧义和材料不完整不会计为 same。离线 `diff` 使用归档的原始计划与当前算法重新解析、匹配，不读取网络配置、不解析凭据、不连接数据库。

## 导出目录中的文件

| 文件或目录 | 用途 |
|---|---|
| `manifest.json` | 材料包清单，记录来源、版本、库表对象、状态以及文件 hash；读取包时用于完整性核验。 |
| `queries.jsonl` | 每行一个 SQL 样例，含原 SQL、默认库、参数、来源、指标、依赖和样例问题。 |
| `selection.json` | 时间范围、筛选条件、排名维度及 Top N 等选择依据；显式 SQL 输入会记录相应输入模式。 |
| `context.json` | 源版本、采集会话、SQL/Status 身份、Bindings、TiFlash 和采集覆盖等上下文。 |
| `metadata_backup/` | 导出的建库、建表、建视图 DDL；不含业务表数据。 |
| `statistics_backup/` | Status API 导出的原始统计 JSON，用于 `LOAD STATS`；不含业务行数据。 |
| `table_list.txt` | 入包的依赖对象清单，采用带库名的对象名。 |
| `skipped_tables.txt` | 带对象信息的跳过或异常条目；为空不代表其他问题均不存在。 |
| `issues.jsonl` | SQL 样例、依赖、统计或采集过程中记录的问题；用于解释 `partial`。 |
| `checkpoint.json` | 输出检查点，记录本次指纹及已写文件的校验信息；不是数据库备份。 |
