# PCC Core

**把同一批 SQL 放到升级前、升级后的 TiDB 中，比较执行计划，并生成 HTML 报告。**

当前版本：**0.1.4** · [下载与所有版本](https://github.com/jielun132/pcc_kernel/releases) · [更新记录](CHANGELOG.md)

下面按一个完整 Demo 走四步。准备三套相互独立的 TiDB：`prod` 模拟生产环境，`old` 模拟升级前，`new` 模拟升级后。PCC 只对待比较 SQL 执行普通 `EXPLAIN`，不会执行业务 DML。

## 1. 部署工具，填好连接信息

在能连接三套 TiDB 的 **Linux x86_64** 机器上执行，使用 Bash。原生包自带 Python；运行机需要 glibc 2.28 或以上，导入步骤另需 MySQL 8 客户端。

安装包和校验文件见 [v0.1.4 Release](https://github.com/jielun132/pcc_kernel/releases/tag/v0.1.4)；从源码开发请参考[本地开发说明](CONTRIBUTING.md#本地开发)。

```bash
curl -fLO https://github.com/jielun132/pcc_kernel/releases/download/v0.1.4/pcc-core-0.1.4-linux-x86_64.tar.gz
tar -xzf pcc-core-0.1.4-linux-x86_64.tar.gz
cd pcc-core-0.1.4-linux-x86_64
./pcc --version
cp examples/profiles.demo.json profiles.json
vi profiles.json
```

配置里只有三组连接，填入自己的 `host`、`port`、`user`、`status_url`。`status_url` 是对应 TiDB 节点的 HTTP Status 地址，常用端口是 `10080`。

| 配置名 | 对应环境 | 用途 |
|---|---|---|
| `prod` | 模拟生产 | 跑演示 SQL，导出材料 |
| `old` | 升级前的临时环境 | 导入材料，获取旧计划 |
| `new` | 升级后的临时环境 | 导入材料，获取新计划 |

本例使用**无 TLS、三套环境相同的数据库密码**。下面所有 `mysql` 命令中的示例主机、端口、用户，也要替换成配置中对应的值。不同密码或 TLS 配置见 [操作说明](docs/operations.md)。

```bash
read -r -s -p '请输入 TiDB 数据库密码：' PCC_DB_PASSWORD
echo
export PCC_DB_PASSWORD
export MYSQL_PWD="$PCC_DB_PASSWORD"
export TZ=Asia/Shanghai
```

## 2. 在 prod 跑几条 SQL，再按时间导出

下面新建一个专用演示库，写入 3 个客户、320 条订单，再执行包含聚合、Join、Hint 的 6 条查询。`examples/demo.sql` 只操作选定的演示库。

```bash
DEMO_DB="pcc_demo_$(date +%Y%m%d_%H%M%S)"
DEMO_DIR="$PWD/demo-output/$DEMO_DB"

mysql -hprod-tidb.example.invalid -P4000 -uroot --ssl-mode=DISABLED \
  -e "CREATE DATABASE \`$DEMO_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;"

PCC_BEGIN=$(date -Is)
mysql -hprod-tidb.example.invalid -P4000 -uroot --ssl-mode=DISABLED \
  "$DEMO_DB" < examples/demo.sql
sleep 1
PCC_END=$(date -Is)

./pcc --config profiles.json export --source prod --from-summary \
  --begin "$PCC_BEGIN" --end "$PCC_END" --schemas "$DEMO_DB" \
  --statement-types select --top-n 10 --out "$DEMO_DIR/bundle"
```

`PCC_BEGIN`、`PCC_END` 是本次演示的开始、结束时间。比较已有业务 SQL 时，跳过造数，改为自己的库名和时间范围，例如 `2026-10-27T09:00:00+08:00` 到 `2026-10-27T10:00:00+08:00`；该时间段需要仍有 Statement Summary 记录。

`--top-n 10` 默认按**执行次数、总耗时、平均耗时、估算总扫描 Key 数、平均扫描 Key 数**各取前 10 个 SQL 摘要，再合并去重。SQL 及其依赖的表结构、统计信息保存到 `bundle`。表结构和统计信息取导出时的当前状态。

## 3. 把同一份结构和统计信息导入 old、new

```bash
./pcc --config profiles.json prepare --bundle "$DEMO_DIR/bundle" \
  --targets old,new --out "$DEMO_DIR/prepared"

mysql -hold-tidb.example.invalid -P4000 -uroot --ssl-mode=DISABLED --local-infile=1 \
  < "$DEMO_DIR/prepared/old/import_commands.sql"
mysql -hnew-tidb.example.invalid -P4000 -uroot --ssl-mode=DISABLED --local-infile=1 \
  < "$DEMO_DIR/prepared/new/import_commands.sql"

sleep 5
```

`prepare` 只生成导入脚本；后面的 `mysql` 才真正导入。`--targets old,new` 区分两个导入目标，材料共用一份，**不按版本转换格式**。

这里不复制业务数据，目标表为空是正常的，**不要在目标环境重新执行 ANALYZE**。`sleep 5` 只是给统计加载留一点时间，不保证加载完成；下一步仍会检查统计信息。

## 4. 比较执行计划，打开报告

```bash
./pcc --config profiles.json compare --bundle "$DEMO_DIR/bundle" \
  --old old --new new --out "$DEMO_DIR/compare" --stats-retries 5
echo "PCC 返回码：$?"
echo "报告位置：$DEMO_DIR/compare/report.html"
unset PCC_DB_PASSWORD MYSQL_PWD
```

把 `report.html` 下载到电脑，用浏览器打开：

- 按“执行计划一致 / 存在差异 / 无法比对”分别统计和筛选，无法比对项直接说明原因。
- 展示差异类型、命中规则及归并事实，估算行数单列；两端计划对齐并保留字段证据。
- 相似差异可集中查看，仍需 DBA 逐条评审，不自动批量通过或继承结论。
- SQL 目录支持分页、搜索和按编号跳转；可按数据库或两侧完整计划结构分组。本次暂不审核的库可隐藏并恢复，原始统计不变，也不计为通过。
- 长计划保留层级缩进、固定列头和框内滚动；旧计划、新计划或两侧文本均可一键复制。
- 返回码 `0` 表示完整比较且一致，`1` 表示完整比较且有变化，`3` 表示部分未完成，查看报告中的原因。计划变化不等于性能变差。

每次重跑请使用新的 `DEMO_DB` 和输出目录，工具不会覆盖已有结果。

## 版本、限制和开发

这是早期版本，尚不支持所有 SQL 场景。0.1.4 的本地 Docker 验证共 413 条 SQL：200 条一致、197 条有差异、16 条无法比对。后者包含 11 条预期拦截和 5 条未解决问题；分区统计回放、长 Prepared EXPLAIN 等限制见[验证说明](docs/validation.md)。

开发中的回归案例集中在 [固定 SQL 用例库](tests/cases/README.md)：413 条、17 类，历史用例只允许追加，CI 校验历史内容。报告交互测试还覆盖 1,053 条 SQL 的分页与分组，需要本机 Node.js；程序运行本身不依赖 Node.js。本地 Docker v6.5.12 / v8.5.5 的用例验证不替代客户版本认证。

[所有 Release](https://github.com/jielun132/pcc_kernel/releases) · [更新记录](CHANGELOG.md) · [详细原理与 SDK](docs/architecture.md) · [操作说明](docs/operations.md) · [源码安装与贡献](CONTRIBUTING.md) · [构建说明](docs/distribution.md)

许可证：[Apache-2.0](LICENSE)。
