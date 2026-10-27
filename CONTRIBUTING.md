# 贡献指南

开发分支为 `main`。请通过 Issue 描述问题或提出设计，再通过 Pull Request 提交范围明确的变更。修复请附脱敏复现与回归验证；不要通过删除测试、跳过已知失败或降低断言来扩大支持声明。

## 本地开发

使用 CPython 3.11：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pytest -q tests/unit
.venv/bin/pcc doctor
.venv/bin/python -m build --no-isolation
```

`src/pcc_tool/` 为 CLI/SDK 共用核心；`tests/unit/` 为离线测试；`tests/integration/` 为需要显式启用的真实集群测试；`tests/fixtures/` 只保存合成数据或脱敏计划。CI 只运行离线检查，不提供数据库凭据。

## 修改约定

- 保留 SQL、Hint、参数和原始计划证据。不要用 AST 重写待测 SQL 来绕过兼容性问题。
- 修改归一化、匹配或差异语义时，同步评估算法版本和恢复指纹兼容性。
- Golden 同时检查应出现的差异与不应出现的误报；未知语义和失败不应变成 same。
- 新依赖保持必要且锁定版本；许可证同时更新到 THIRD_PARTY_NOTICES.md。
- 文档说明实际运行过的验证；模拟连接和合成夹具不能替代真实 TiDB 认证。

## 在线测试

必须由操作者明确授权并配置隔离测试环境。相关脚本会在专用合成测试库内建表、写入夹具、ANALYZE 或 LOAD STATS，不能指向业务库。每次使用新的输出目录。

各套件的开启开关、版本配置和已知失败见 [验证说明](docs/validation.md)。不要向 PR、Issue 或 CI 上传原始连接配置、凭据、内部地址、业务 SQL、统计文件或现场报告。使用不存在的 `*.example.invalid` 主机名和可公开的合成数据复现。

## 发布与打包

wheel / sdist 可在开发环境构建；原生 Linux 包必须在声明的发行版、CPU 与 glibc 基线上构建并验证，流程见 [distribution.md](docs/distribution.md)。构建完成不自动等于兼容性认证。

每次发布都更新 `pyproject.toml`、`src/pcc_tool/__init__.py` 中的版本号，并在 [CHANGELOG.md](CHANGELOG.md) 写明改动、验证结果和仍存在的限制。README 的版本号及下载地址一并更新。

通过 PR 合并且 CI 通过后，在 Actions 手动运行 **Build release assets**。它从当前提交构建 Rocky Linux 8.10 / x86_64 / glibc 2.28 原生包并执行离线冒烟验证。构建通过后，为这个已验证的提交创建 `vX.Y.Z` 标签和 GitHub Release，上传构建产物及校验文件，填写对应版本的更新说明。保留每个历史标签和 Release，不覆盖旧版资产；从 [Releases](https://github.com/jielun132/pcc_kernel/releases) 查看所有版本。

项目采用 [Apache-2.0](LICENSE)。提交贡献时，请确保有权按该许可证贡献相应内容，并保留第三方声明。
