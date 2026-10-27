# Linux 离线交付

默认交付目录是 `pcc-core-<version>-linux-<arch>`，包含 `pcc`、CPython 3.11 运行时、依赖、schemas、离线文档、配置示例、演示报告、许可证和 SHA256SUMS。另附 SDK wheel、锁定依赖和 wheelhouse。wheelhouse 不代替默认客户运行包。

公开安装包在 [GitHub Releases](https://github.com/jielun132/pcc_kernel/releases) 按版本提供。`Build release assets` 工作流在 Rocky Linux 8.10 / x86_64 / glibc 2.28 容器中构建，附带版本检查、doctor 和合成计划的离线 diff 验证；不连接真实 TiDB。实际构建信息见包内 `build-info.json`，完整兼容性以 [验证说明](validation.md) 为准。

可将安装包及同名 `.sha256` 文件下载到同一目录后执行 `sha256sum -c pcc-core-0.1.3-linux-x86_64.tar.gz.sha256`，再解压使用。项目没有发布到 PyPI；Release 中的 wheel 需要 CPython 3.11，普通使用者优先下载自带运行时的 Linux 包。

在我方与声明基线匹配的原生 Linux 构建机上安装 `requirements-dev.lock` 及本项目后执行：

```bash
python3.11 scripts/build_linux.py --arch x86_64 --distro ubuntu:22.04 --glibc 2.35 --out dist/build-001
```

上述发行版仅用于展示命令，并非已认证目标。脚本要求参数与构建机完全一致，拒绝 macOS/Windows 跨平台冒充 Linux 包；输出目录已存在时拒绝覆盖。已有离线依赖源可通过 `--wheelhouse /path/to/wheelhouse` 提供。此脚本只构建本地文件，不部署、不上传、不发布。

正式发布前需在声明的 Linux/CPU/glibc 矩阵内，以普通账号、无 Python/pip、无外网验证：解压启动，doctor，演示离线 diff，TLS 身份校验，SQLGlot 方言动态导入，schema 资源定位，真实双端 compare 和报告查看。包内 `build-info.json` 的 `built_not_release_certified` 明确表示尚未认证。

当前应用只依赖 PyMySQL 与 SQLGlot。PyMySQL 的非 TLS RSA 认证可能需要额外 cryptography 依赖；该认证组合不在当前交付声明内，使用经过环境验证的 TLS/认证方式，不自动静默降级 TLS 验证。

客户流程：解压 tar.gz、校验 SHA256SUMS、配置连接及密码环境变量引用、运行 `./pcc`。人工导入 DDL/LOAD STATS 的 MySQL 客户端属于 DBA 数据库操作流程，PCC 不调用外部 mysql。跨机移动导入材料时必须审核并调整 `LOAD STATS` 的绝对路径；禁止修改 stats JSON 内容绕过失败。
