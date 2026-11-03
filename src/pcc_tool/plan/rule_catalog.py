"""Versioned, offline rule cards. A card is not evidence that its premise holds."""

ASSESSMENT_VERSION = "3"
DEFAULT_THRESHOLDS = {"min_rows": "1000", "max_ratio_pct": "10", "reduction_pct": "50", "estimate_pct": "10"}


def card(key, title, better, worse, requires, source, *, automatic=False, default=True, operators=()):
    return dict(id=key, title=title, group="equivalent" if key.startswith("E") else "review" if key.startswith("C") else "directional",
                better=better, worse=worse, requires=requires, source=source if source.startswith("https://") else "https://pingkai.cn/docs/tidb/stable/" + source,
                automatic=automatic, default=default, operators=list(operators))


RULE_CATALOG = [
    card("A01", "唯一键点查", "相同唯一键单点范围 → Point_Get", "反向变化", "同一整数主键、相同键值、单表查询、完整 DDL；批量点查仍需复核。", "choose-index", automatic=True, operators=("Point_Get", "Batch_Point_Get")),
    card("A02", "覆盖索引消除回表", "同索引同范围 IndexLookUp → IndexReader", "反向变化", "覆盖所需列；无回表端过滤；索引扫描量、顺序不变。", "choose-index", automatic=True, operators=("IndexLookUp", "IndexReader")),
    card("A03", "回表前过滤", "同一谓词移到索引端，减少回表候选", "过滤移回表端，候选增加", "相同索引及确定性整数比较谓词，统计可信，候选减少达到阈值。", "predicate-push-down", automatic=True, operators=("IndexLookUp",)),
    card("A04", "大表选择性范围访问", "全扫描 → 高选择性范围扫描", "反向变化", "至少 10000 行，候选不超过 10%；需要完整谓词与范围对应，排除 LIMIT、顺序收益和引擎切换。", "choose-index", operators=("TableFullScan", "IndexFullScan", "TableRangeScan", "IndexRangeScan")),
    card("A05", "相同索引范围收敛", "等价过滤下范围缩小", "范围扩大", "需要边界包含关系、剩余谓词、类型和排序规则证明；不能只比较 estRows。", "choose-index", operators=("IndexRangeScan", "TableRangeScan")),
    card("A06", "有效谓词下推", "相同 Selection 从 root 下推 TiKV，减少传输", "反向变化", "相同扫描及确定性整数比较谓词，不跨 Join；输入及减少比例达到阈值，非 pseudo 统计。", "predicate-push-down", automatic=True, operators=("Selection",)),
    card("A07", "传输列裁剪", "同一纯列投影下推 TiKV，传输列减少", "相同列裁剪移回 root，传输列增加", "逐项核对剩余计划；仅自动接受下推端 v8.5.5，其源码要求纯列投影减少列数。小输入收益可能很小。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/expression/util.go#L1550", automatic=True, operators=("Projection",)),
    card("A08", "TopN 下推", "保留最终 TopN，同时增加 TiKV 局部 TopN", "反向变化", "排序键、方向、offset/count 一致；输入与 K/输入比例满足阈值；不把每分片 K 当全局 K。", "topn-limit-push-down", automatic=True, operators=("TopN",)),
    card("A09", "Limit 提前截断", "保留最终 Limit，增加直接扫描端 Limit", "反向变化", "相同 offset/count，无跨过滤或 Join；输入与 K/输入比例满足阈值。", "topn-limit-push-down", automatic=True, operators=("Limit",)),
    card("A10", "有序 IndexMerge 避免 TopN", "主键有序合并，TopN 改为同参数内嵌 Limit", "恢复额外 TopN", "聚簇主键排序；各索引单列等值前缀且有序，输入路径及 offset/count 相同。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/task.go#L793", automatic=True, operators=("TopN", "IndexMerge")),
    card("A11", "有界排序", "Sort + Limit → TopN", "反向变化", "同排序键、方向和 offset/count；输入与 K/输入比例满足阈值。", "topn-limit-push-down", automatic=True, operators=("TopN", "Sort")),
    card("A12", "部分聚合下推", "局部聚合减少传输，保留最终聚合", "反向变化", "单个无 GROUP BY/DISTINCT 的 COUNT/SUM/MIN/MAX；partial/final 映射相同，至少 10000 行且传输不超过 10%。", "explain-aggregation", automatic=True, operators=("HashAgg", "StreamAgg")),
    card("A13", "MIN/MAX 端点访问", "完整聚合 → 有序索引端点访问", "反向变化", "需要排序、NULL、空集语义及端点访问证明。", "max-min-eliminate", operators=("HashAgg", "StreamAgg", "Limit")),
    card("A14", "唯一键聚合消除", "唯一键保证每组至多一行，消除聚合", "增加冗余聚合", "需要输入子树的唯一性及聚合表达式等价证明，不能只看表主键。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/rule_aggregation_elimination.go", operators=("HashAgg", "StreamAgg")),
    card("A15", "冗余 DISTINCT 消除", "唯一性已保证时消除去重", "增加冗余去重", "需要 Join 后唯一性、NULL 及 DISTINCT 表达式证明。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/rule_aggregation_elimination.go", operators=("HashAgg", "StreamAgg")),
    card("A16", "冗余外连接消除", "不用内侧列且唯一匹配时消除外连接", "增加冗余外连接", "需要列使用、连接键唯一性、行数及 NULL 扩展证明。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/rule_join_elimination.go", operators=("HashJoin", "IndexJoin", "MergeJoin")),
    card("A17", "恒假分支消除", "SQL 可证明恒假，扫描分支 → 空 TableDual", "反向变化", "仅简单 SELECT WHERE FALSE/0/常量不等式；不能把空测试库或 estRows=0 当证明。", "predicate-push-down", automatic=True, operators=("TableDual",)),
    card("A18", "分区裁剪", "同定义、同谓词下实际访问分区集合缩小", "集合扩大", "需要明确分区集合、分区 DDL 和谓词对应；static/dynamic 切换不直接判优。", "partition-pruning", operators=("PartitionUnion", "TableFullScan", "TableRangeScan")),
    card("A19", "ROW_NUMBER 上界提前截断", "受支持的窗口上界推导出提前 TopN", "反向变化", "需要单个 ROW_NUMBER、上界、分区键和数据源证明；不扩展到 RANK/DENSE_RANK。", "derive-topn-from-window", operators=("Window", "TopN")),
    card("A20", "NULL 感知反半连接", "无哈希键的反半连接 → Null-aware 哈希连接", "退回笛卡尔候选过滤", "相同 eq、输入和 Build/Probe，保留 NOT IN 的 NULL 语义；不套用普通反连接。", "explain-subqueries", automatic=True, operators=("HashJoin",)),
    card("A21", "NULL 安全等值哈希键", "nulleq 从 other cond 提升为半连接哈希键", "哈希键退回候选过滤", "完整 nulleq 条件、输入、角色和剩余计划相同；可与纯列下推组合。", "explain-joins", automatic=True, operators=("HashJoin",)),
    card("A22", "综合工作量预判（估算）", "同一 PCC 模型综合工作量下降超过容差", "综合工作量上升超过容差", "支持已知 Join/访问路径/窗口/截断取舍；同一权重计算扫描、回表、传输、CPU、内存，max/min ≤ 1.20 视为基本等价。DDL 宽度有界、统计就绪、已核验版本才评分。是启发式预判，不是实测或 TiDB estCost；详情公开各项分数。", "cost-model", automatic=True, operators=("HashJoin", "IndexJoin", "IndexHashJoin", "IndexMergeJoin", "MergeJoin", "Window", "IndexMerge", "Limit")),
    card("E01", "列别名展示变化", "基本等价：同位置同来源列，仅增加 →Column# 标记", "不据此判断变差", "只接受纯列透传标记；计算表达式、列增删、未解析映射不放行。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/expression/explain.go#L182", automatic=True, operators=("Projection",)),
    card("E02", "纯透传 Projection", "基本等价：相邻重复透传 Projection 增删", "不据此判断变差", "完整列列表、顺序和执行位置相同；跨 root/cop 可能改变传输或计算，不能直接当等价。", "column-pruning", automatic=True, operators=("Projection",)),
    card("E03", "简单谓词展示等价", "基本等价：白名单纯整数比较谓词换序", "不据此判断变差", "仅确定性整数比较和 AND；不归一化字符串、NULL、函数、隐式转换或 OR。", "predicate-push-down", automatic=True, operators=("Selection",)),
    card("E04", "显式默认字段", "基本等价：经版本核验的省略默认值补全", "不据此判断变差", "目前不自动忽略字段；需要具体版本与默认值证据，未知字段保持待复核。", "explain-overview"),
    card("E05", "仅估算值小幅波动", "按阈值视为基本等价", "超过阈值保持待复核", "默认不选；物理计划完全相同，估算均为正值且 max/min 在容差内；不等于实测性能相当。", "statistics", automatic=True, default=False),
    card("E06", "聚合输入列来源映射", "基本等价：重复纯列与槽位编号变化", "表达式、聚合算法或输出变化不适用", "核验注入 Projection 的连续输出槽位及所有显式锚点，逐个比较聚合输入；限定已核验的 v6.5.12 / v8.5.5。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/rule_inject_extra_projection.go", automatic=True, operators=("HashAgg", "StreamAgg")),
    card("E07", "相关索引的冗余过滤", "基本等价：动态等值范围已蕴含相同上界", "范围或回表规模改变不适用", "整数外侧聚簇主键上界、动态等值键与内侧谓词逐项核对；不把相关扫描估算变化视为实际 I/O。", "predicate-push-down", automatic=True, operators=("Selection", "IndexRangeScan")),
    card("E08", "DECIMAL 零常量展示", "基本等价：IF 的零分支补足小数位", "非零、类型或表达式变化不适用", "由同一 DECIMAL 列决定分支类型，小数位与 DDL 一致，其他表达式相同。", "data-type-numeric", automatic=True, operators=("Projection",)),
    card("E09", "统计诊断展示", "基本等价：未初始化列统计诊断及过滤估算传播", "访问路径、范围或谓词改变不适用", "仅允许 stats:partial[column:unInitialized] 诊断；相同扫描和谓词上的过滤/透传估算可随诊断改变。保留统计警告，不据此判断实际扫描量或统计质量。", "statistics", automatic=True, operators=("TableFullScan", "IndexFullScan")),
    card("E10", "唯一匹配外连接的冗余 Limit", "基本等价：顶层重复截断增删", "连接可能放大行数时不适用", "内侧是唯一聚簇主键匹配，外侧内嵌相同 offset=0 的 Limit；完整核对其余连接计划。", "topn-limit-push-down", automatic=True, operators=("Limit", "IndexHashJoin")),
    card("E11", "版本估算下限变化", "基本等价：小于一行的估算下限及透明传播", "真实访问路径或大幅估算变化不适用", "v6.5.12 / v8.5.5 已核验版本对；全部物理差异另有完整证明，保留原始估算。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/cardinality/row_count_index.go#L368", automatic=True),
    card("E12", "ASCII 排序权重键展示", "基本等价：等值范围文字与排序权重编码", "其他字符或排序规则不适用", "SQL 显式 utf8mb4_general_ci，ASCII 字母/数字/连字符；逐字核对权重编码、索引列和范围，限定已核验版本对。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/util/collate/general_ci.go#L32", automatic=True, operators=("IndexRangeScan",)),
    card("E14", "长绑定常量展示缩略", "基本等价：完整字符串与精确缩略", "缺完整参数、前缀或字节长度不符不适用", "单个字符串绑定的等值谓词，完整参数与旧计划一致；新计划前 64 字节、长度与 v8.5.5 源码完全一致。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/types/datum.go#L579", automatic=True, operators=("Selection",)),
    card("E15", "Shuffle 接收边界展示", "基本等价：显式 ShuffleReceiver", "并发、排序或输入变化不适用", "两端相同 Shuffle 上下文，完整核对 Window、Sort、输入及估算；限定已核验版本对。", "https://github.com/pingcap/tidb/blob/v8.5.5/pkg/planner/core/flat_plan.go#L369", automatic=True, operators=("ShuffleReceiver",)),
    card("C01", "索引替换", "需结合选择性、覆盖、顺序判断", "没有固定优劣顺序", "同名/不同名索引都需比较完整访问成本。", "choose-index", operators=("IndexRangeScan", "IndexFullScan")),
    card("C02", "Join 算法切换", "待复核", "待复核", "Hash/Index/Merge Join 优劣依赖规模、索引、内存及并发。", "explain-joins", operators=("HashJoin", "IndexJoin", "IndexHashJoin", "IndexMergeJoin", "MergeJoin")),
    card("C03", "Join 顺序与 Build/Probe", "待复核", "待复核", "需要中间行数、构建侧大小、数据倾斜及执行证据。", "join-reorder", operators=("HashJoin", "IndexJoin", "MergeJoin")),
    card("C04", "HashAgg / StreamAgg", "待复核", "待复核", "需要分组数、输入顺序、额外排序和并行度。", "explain-aggregation", operators=("HashAgg", "StreamAgg")),
    card("C05", "IndexMerge", "待复核", "待复核", "需要各索引范围、合并去重和回表规模。", "explain-index-merge", operators=("IndexMerge",)),
    card("C06", "TiKV / TiFlash / MPP", "待复核", "待复核", "引擎改变不能作为固定收益；需要数据量、同步、并发和网络。", "use-tiflash-mpp-mode", operators=("ExchangeSender", "ExchangeReceiver")),
    card("C07", "Broadcast / Shuffle", "待复核", "待复核", "需要两侧规模、节点数、倾斜及传输量。", "use-tiflash-mpp-mode", operators=("ExchangeSender", "ExchangeReceiver")),
    card("C08", "Apply 去关联", "待复核", "待复核", "重复探测和一次性聚合各有成本，不能一律认定 Join 更好。", "correlated-subquery-optimization", operators=("Apply",)),
    card("C09", "CTE 内联 / 物化", "待复核", "待复核", "需要复用次数、过滤下推及中间结果大小。", "sql-statement-with", operators=("CTE", "CTEFullScan")),
    card("C10", "并行窗口 / Shuffle", "待复核", "待复核", "需要排序、分区规模、并行开销和数据倾斜。", "window-functions", operators=("Shuffle", "ShuffleReceiver", "Window")),
    card("C11", "Runtime Filter", "待复核", "待复核", "有过滤器不等于有效；需要选择性、构建和等待开销。", "runtime-filter"),
    card("C12", "分区访问模式", "待复核", "待复核", "static/dynamic 的计划展示不同不等于少访问分区。", "partition-pruning", operators=("PartitionUnion",)),
    card("C13", "计算型 Projection 下推", "待复核", "待复核", "区分计算移动和列裁剪，需要计算、网络、存储负载证据。", "column-pruning", operators=("Projection",)),
]

DEFAULT_RULES = [rule["id"] for rule in RULE_CATALOG if rule["default"]]
