# PCC Core

[![CI](https://github.com/jielun132/pcc_kernel/actions/workflows/ci.yml/badge.svg)](https://github.com/jielun132/pcc_kernel/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)

**面向 TiDB 升级的 SQL 采集、材料导出和执行计划比较工具。**

Offline-first TiDB execution plan capture and comparison, with a shared Python CLI and SDK.

PCC Core 从 Statement Summary 或给定 SQL 清单导出 SQL、表结构、统计信息和上下文，在两套测试环境获取普通 `EXPLAIN`，输出可离线阅读的 HTML、JSONL 和 CSV 报告。CLI `pcc` 与 SDK `pcc_tool.api` 共用核心，可独立使用，也可接入后续平台。

当前版本 **0.1.2，处于早期迭代阶段**。已完成三套集群的实测，但尚未全部通过：分区统计完整回放、TiDB 6.5 的部分 Prepared SQL、二进制参数对照仍有明确限制。请先阅读 [验证结果与已知问题](docs/validation.md)。本项目是独立实现，不依赖旧 Go PCC。

## 能做什么

- **筛选 SQL**：按执行次数、总延迟、加权平均延迟、估算总扫描键数、加权平均扫描键数分别取 Top N，再取并集。
- **导出材料**：保留原 SQL、Hint、显式参数、表/视图依赖、原始统计 JSON 和上下文，生成带校验和的 CaptureBundle。
- **持续采集**：可选 `collect --watch` 写入文件队列，重启后继续采集，再按业务窗口导出。
- **比较计划**：采集 row / tidb_json，统一解析、匹配节点并保留已识别差异；未知语义、缺样例和失败单独列出。
- **离线重算**：`diff` 从归档的原始计划重新比较，不需要数据库、配置文件或凭据。
- **开发集成**：Python SDK、JSONL 事件、取消及带指纹校验的断点恢复。

PCC 不执行业务 SQL 或 `EXPLAIN ANALYZE`。`prepare` 生成供 DBA 审核的导入材料，由 DBA 人工执行 DDL / LOAD STATS；工具不自动导入、恢复业务数据、下发 Binding 或部署数据库。计划差异是事实证据，不能直接解释成性能回退百分比。

## 快速开始：先看离线报告

要求 **CPython 3.11**。目前从源码安装；不要将包名理解为已经发布到 PyPI。

```bash
git clone git@github.com:jielun132/pcc_kernel.git
cd pcc_kernel
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/pcc doctor

# 使用合成数据，经过实际解析、比较及报告生成流程；无需 TiDB。
.venv/bin/python scripts/make_demo.py --out demo-output/example
```

用浏览器打开 `demo-output/example/report.html`。演示有一条计划变化、一条相同计划和一条缺 SQL 样例，便于查看报告如何区分差异与覆盖不足；它不是集群兼容性测试。

重新计算已归档的计划：

```bash
.venv/bin/pcc diff --result-dir demo-output/example --out demo-output/recomputed
```

这个演示保留了缺样例，`diff` 返回 **3** 是预期的部分覆盖结果。输出目录不可覆盖，重跑时请使用新目录。

## 连接 TiDB 的完整流程

1. 复制 [配置示例](examples/profiles.example.json) 为私有 `profiles.json`，填写源库和两套隔离测试环境。
2. 通过配置中的 `password_env` 引用已有环境变量；不要在 JSON、命令参数或 Git 仓库中保存密码。
3. `old` / `new` 明确配置 `role: "test"`，使用不同、固定的 TiDB SQL 端点。`expected_version` 填写现场实际 Release Version，不能混用 Enterprise Core Version。
4. SQL 与 Status 地址需要验证同源；TLS、权限和统计加载要求见 [操作说明](docs/operations.md)。

以下示例假设 `pcc` 已在 PATH 中；源码安装也可使用 `.venv/bin/pcc`。

```bash
# 时间窗口仅是示例，请换成源端实际可见的窗口。
pcc --config profiles.json export --source prod --from-summary \
  --begin '2026-10-08T09:00:00+08:00' --end '2026-10-08T11:00:00+08:00' \
  --top-n 20 --out bundle/run-001
pcc --config profiles.json prepare --bundle bundle/run-001 \
  --targets old,new --out preparation/run-001

# DBA 审核后，将同一份表结构和统计信息人工导入两端。
# 已有对象先核验，跨机器移动材料时审核 LOAD STATS 文件路径。
pcc --config profiles.json doctor --bundle bundle/run-001 --targets old,new
pcc --config profiles.json compare --bundle bundle/run-001 \
  --old old --new new --out results/run-001
pcc diff --result-dir results/run-001 --out results/offline-001
```

比较结果包括 `report.html`、`summary.json`、原始计划、IR、差异、问题及 CSV。结果含 SQL、参数和统计信息，应按业务数据管理，不应直接提交到公开仓库。

| 退出码 | 含义 |
|---|---|
| `0` | 完整比较，无差异 |
| `1` | 完整比较，有差异；属于正常业务结果 |
| `2` | 配置、契约或整体前置条件失败 |
| `3` | 部分失败或覆盖不足，优先于差异状态 |
| `4` | 运行中断 |

## Prepared SQL、Hint 和长 SQL

JSONL 输入可携带完整模板和明确的参数样例：

```json
{"schema":"app","sql_text":"SELECT /*+ USE_INDEX(o,PRIMARY) */ * FROM orders o WHERE id=?","parameters":[{"type":"int","value":"7"}]}
```

PCC 保留原模板与 Hint，将 `EXPLAIN 原模板` 做成服务端 Prepared Statement，通过类型化参数执行 EXPLAIN；不会把问号直接替换成字符串。同一模板的不同参数需要分别提供一行。

支持 `int`、`uint`、`decimal`、`double`、`string`、`bytes`、`date`、`datetime`、`time`、`json`、`bool`、`null`。除 bool / null 外，值使用 JSON 字符串；bytes 为十六进制。具体约束见 [参数与输入契约](docs/architecture.md#sql-与参数)。

这条路径重新优化指定参数下的 SQL，**不复现 Prepared Plan Cache 命中，也不模拟所有应用驱动的二进制编码**。应用发送 JSON 文本时应使用 `string`；`json` 表示 SQL JSON 类型。仅有归一化 digest、缺参数或截断文本时会要求补充样例，不猜测参数。

实测包含 Hint、CTE、Join、窗口函数、DML、约 1 MiB SQL、20,000 项 IN、128 分支 UNION ALL、256 参数及长参数传输。**6.5.3 / 6.5.12 的 `PREPARE EXPLAIN ... IN (?)` 仍会失败**，不能据此声称所有 Prepared SQL 都已支持。INSERT / REPLACE 需显式启用 `--statement-types select,update,delete,insert,replace`。

## 持续采集与时间窗口

```bash
pcc --config profiles.json collect --source prod --watch \
  --interval-seconds 300 --out spool/prod
pcc --config profiles.json export --source prod --spool spool/prod \
  --begin '2026-10-08T09:00:00+08:00' --end '2026-10-08T11:00:00+08:00' \
  --out bundle/history
```

这是 PCC 自身的文件持久化，不要求启用 TiDB 原生 Summary 持久化。时间筛选采用 `[begin,end)` 与 Summary 原始窗口相交的累计值，不是精确分钟级流量，不按重叠时长摊分。重复累计快照不会直接相加；缺样例仍计入完整比较率分母。

`export` 的 `--from-summary`、`--spool`、`--repository`、`--legacy-package`、`--sql-file` 互斥。历史 SQL 与当前表结构/统计信息的时间可能不同；完全离线导出需用 `--dependencies-bundle` 提供已有依赖。旧格式及窗口口径见 [架构与契约](docs/architecture.md)。

## Python SDK

```python
from threading import Event
from pcc_tool.api import compare, diff
from pcc_tool.capture.connection import load_profiles
from pcc_tool.models import CompareRequest, DiffRequest

result = compare(
    CompareRequest(bundle="bundle/run-001", old="old", new="new", out="results/run-001"),
    profiles=load_profiles("profiles.json"),
    on_event=print,
    cancel=Event(),
)
print(result.summary, result.exit_code)
offline = diff(DiffRequest(result_dir="results/run-001", out="results/offline-001"))
```

模块导入不联网。平台可调用 SDK 或使用 argv 列表启动 CLI；`--events jsonl` 将事件写入 stdout，诊断写入 stderr。取消、恢复和上下文核验见 [操作说明](docs/operations.md)。

## 测试与兼容性

2026-10-15 对 6.5.3、6.5.12、Enterprise Release 7.1.9-0.3 / Core 8.5.5 的最终在线检查为 **1,233 通过、27 失败**；368 项离线单元测试通过。原生 Linux 包在 CentOS 8 / x86_64 / glibc 2.28 做过验证。这里没有将 8.5.5 的结果表述为 8.5.8 认证，也不承诺所有 TiDB SQL、TiFlash / MPP 或 Linux 环境均已支持。

公开仓库保留合成数据、脱敏计划夹具和严格失败断言；原始服务器配置、内部设计链接和现场证据不随源码发布。CI 运行离线测试及打包检查，不连接真实集群。详见 [验证结果](docs/validation.md)。

## 开发与文档

- [架构、数据流与契约](docs/architecture.md)
- [操作说明与安全边界](docs/operations.md)
- [验证结果、已知问题及在线测试入口](docs/validation.md)
- [Linux 离线构建](docs/distribution.md)
- [贡献指南](CONTRIBUTING.md) · [安全问题报告](SECURITY.md)

默认开发分支为 `main`。欢迎通过 Issue 提交脱敏复现，或通过 Pull Request 提交修复和回归测试；已知失败优先于扩大兼容性声明。

## 许可证

本项目按 [Apache License 2.0](LICENSE) 开源。第三方组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
