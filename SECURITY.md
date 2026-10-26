# 安全问题报告

请使用仓库的 [Private vulnerability reporting](https://github.com/jielun132/pcc_kernel/security/advisories/new) 私下报告安全问题，不要在公开 Issue 中粘贴凭据或未脱敏的业务材料。

报告请包含受影响版本、最小复现、影响范围及可公开的合成样例。不要附数据库密码、Token、私钥、服务器登录信息、原始业务 SQL 或统计文件。

目前仅维护 `main` 分支，不承诺其他版本的安全更新周期。发现问题后将验证影响并在修复中补充回归测试。

PCC 的导出包与报告会包含 SQL、参数、表结构和统计上下文；离线文件不等于无敏感数据。真实测试需要独立隔离环境，普通 EXPLAIN 的优化阶段也可能涉及子查询计算，应保留权限限制与超时。使用前请阅读 [操作边界](docs/operations.md)。
