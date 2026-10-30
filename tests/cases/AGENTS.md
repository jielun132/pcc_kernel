# 用例库维护约定

- `sql_cases.jsonl` 和 `catalog.lock.json` 只允许在末尾追加，禁止修改、删除或重排历史记录。
- 每条用例使用永久唯一的 `SQL-NNNN` 编号，保留 SQL、参数、预期校验结果、覆盖目的和标签。
- 发现历史用例有误时，追加修正案例并写明关联编号与原因；不要替换或弱化旧用例。
- 追加时同步增加 `catalog.lock.json` 的逐行 SHA-256 和 `SQL_CASES.md` 的人工可读说明。
- 不以调整预期、删除断言、跳过失败用例的方式让测试通过；失败要保留证据并修复。
- 执行 `python scripts/check_case_catalog.py` 和 `python -m pytest -q tests/unit/test_case_catalog.py`。
- 历史编号、行内容和锁文件会与 Git 基线一起校验。现有 `tests/sql_matrix.py` 及测试继续保留。
