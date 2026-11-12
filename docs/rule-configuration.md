# 0.1.7 规则配置与人工评审

规则配置只影响预判，不改原始计划、差异和 CLI 返回码。普通 EXPLAIN 的结构收益不等于实测性能保证。

## 使用外部配置

复制 `examples/rules.example.json` 为自己的规则文件。在 `pcc.json` 的 `workflow` 增加：

```json
{
  "rules_config": "rules.json",
  "rule_profile": "auto"
}
```

路径相对于 `pcc.json`。菜单 8 可选择版本规则方案，下次比较或菜单 7 离线重算时生效。版本方案只选择评审规则；数据库连接仍使用 `profiles.old/new`，精确核对 `expected_version`。

CLI 示例：

```bash
./pcc --config pcc.json compare --bundle bundle/run-001 --old old --new new \
  --rules-config rules.json --rule-profile auto --out results/run-001
./pcc diff --result-dir results/run-001 --out results/offline-001
```

SDK 的 `CompareRequest` 和 `DiffRequest` 支持相同的 `rules_config`、`rule_profile` 参数。离线重算省略配置时继承归档，不要求原配置文件仍在原路径；显式提供新文件时使用新配置。

| 配置项 | 含义 |
|---|---|
| `schema_version` | 当前为整数 `1` |
| `default_profile` | `auto` 或明确的方案 ID |
| `selected` | 已知规则 ID 数组，省略时使用推荐选择；空数组取消可选规则，D01/D02/D08/D10 仍生效 |
| `thresholds` | `min_rows` 1–1000000000，`max_ratio_pct` 1–50，`reduction_pct` 1–99，`estimate_pct` 1–50；整数 |
| `cards` | 按规则 ID 定义 `title`、`description`、`review_note`；文本在 HTML 中转义显示 |
| `profiles` | 方案列表，每项含 `id`、`label`、`old_version`、`new_version`，可另外指定 `selected` 和 `thresholds` |

内置 `tidb-6.5.12-to-8.5.7` 和通用 `default`。自动匹配按精确版本进行，保留补丁和发行版后缀；未知组合继承 `default`。版本专属证明仍检查实际采集版本，选择方案不能把未验证版本伪装成已验证版本。

新增版本方案示例：

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "id": "customer-upgrade",
      "label": "客户升级评审",
      "old_version": "6.5.12",
      "new_version": "8.5.8",
      "selected": ["A01", "A02", "E01", "E02"],
      "thresholds": {"min_rows": 2000}
    }
  ]
}
```

此例仅展示配置方式，不表示 8.5.8 已完成验证。未知字段、未知卡号、重复版本组合、越界阈值会拒绝加载。配置不支持修改执行代码、强制结论、覆盖证明条件或关闭必审条件；新增判断算法需代码实现和正反例验证。

报告的“版本规则方案”下拉框可在归档中已有的方案间切换。切换或调整阈值后，点击“按所选规则重新判断”生效。全部 SQL 都会重新判断，当前搜索和分页不缩小判断范围。`assessment_rules.json` 保存生效配置及指纹，JSON/CSV 导出记录实际应用的方案、规则和阈值。

## 人工评审

1. 应用规则后，在每条 SQL 的“人工评审”中选择结论、填写备注，再勾选“已评审”。自动结论始终单独保留。
2. 上方可筛选全部、未完成评审、已评审和需重新确认。搜索、分页和分组不会传播勾选或结论。
3. 修改规则、阈值或版本方案后，旧勾选需要重新确认；原结论和备注保留。修改尚未应用时不能确认评审。
4. 当前浏览器会尝试本地保存。文件 URL 的存储受浏览器限制，换电脑、移动报告或交付前，请点击“保存评审 JSON”。浏览器拒绝保存时页面会明确提示。
5. 在同一报告点击“恢复评审”选择 JSON；整个文件通过报告指纹、SQL 身份、重复项和字段校验后才合并。同一 SQL 以导入记录为准，未包含的记录保留。其他报告的文件会拒绝导入。
6. “导出判断 JSON/CSV”包含自动判断及人工勾选、人工结论、备注、需重新确认状态。评审 JSON 是恢复文件；CSV 用于表格审阅。

重新打开报告时，规则选择恢复为生成报告时的配置。若评审时调整过规则，请重新应用相同的方案、勾选和阈值；在评审依据一致前，旧勾选显示为需重新确认，备注保留。

不同输入的报告不能套用旧评审文件。相同输入重新采集时，原始计划或比较证据变化也会使旧勾选失效，即使自动分类未变；备注仍保留，避免把针对旧计划的确认当成新计划的确认。

## 预热和统计复查

菜单 2/3 导入后，先执行一轮普通 EXPLAIN，单独存入任务的 `warmup/`。正式报告从第二轮独立采集产生。中断后选菜单 4，复用对应阶段已经保存的有效观测；已完成的预热不会再次计算为比较结果。0.1.6 已完成的固定材料预热仍可读取。

正式采集看到 `pseudo`、`partial` 或统计加载超时，会在原重试预算内保存观测并复查。若仍不能取得完整统计，SQL 保留无法完整比对和具体原因。生成列等缺少统计的情况不能靠反复 EXPLAIN 补出统计；需要核查源材料及版本行为，不能静默删除诊断以制造稳定结果。

CLI/SDK 也可使用 `--warmup-only` / `warmup_only=True` 单独预热；输出是 `PlanWarmup`，不能作为 `diff` 的正式输入。
