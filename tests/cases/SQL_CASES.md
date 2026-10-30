# PCC SQL 回归用例库

本文件列出全部固定用例，便于 DBA 逐条检查。完整机器可执行内容在 `sql_cases.jsonl`，每条保留固定 ID；此文件及目录只追加，不替换旧案例。

逻辑库：`pcc_case_business`、`pcc_case_reporting`、`pcc_case_archive`。运行时映射为本次独立的数据库名。DML 只做 EXPLAIN，不执行实际写入。

本轮共 413 条：402 条预期通过 SQL 校验，6 条缺少可执行样例，5 条应被拒绝。预期通过校验不等于执行计划一致；实际双版本捕获、比较结果另行记录。

| 分类 | 数量 |
|---|---:|
| 语法与 DML | 39 |
| 数据类型 | 20 |
| 优化器 Hint | 25 |
| Prepared 参数类型 | 24 |
| 超长 SQL 与参数 | 7 |
| 谓词 × 投影 / 排序 / 聚合 | 72 |
| 连接类型 × 过滤位置 × 排序 | 45 |
| 分组键 × 聚合函数 × 条件 | 48 |
| 子查询 × 外层条件 | 21 |
| 窗口函数 × 分区 × 窗口范围 | 24 |
| 集合操作 × 排序 / TopN | 12 |
| 参数化组合 × 参数分布 | 24 |
| 多库分组 | 24 |
| 跨库连接 | 6 |
| 视图与嵌套视图 | 4 |
| 长计划：宽树与深树 | 7 |
| 不应执行的边界反例 | 11 |

## SQL-0001 · left_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id<20
```

## SQL-0002 · right_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o RIGHT JOIN customers c ON c.id=o.customer_id WHERE c.id<20
```

## SQL-0003 · cross_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.id FROM orders o CROSS JOIN customers c WHERE o.id<3 AND c.id<3
```

## SQL-0004 · straight_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o STRAIGHT_JOIN customers c ON c.id=o.customer_id WHERE o.id<20
```

## SQL-0005 · join_using

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders JOIN customers USING(id) WHERE id<20
```

## SQL-0006 · derived_union

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT x.id,c.name FROM (SELECT id,customer_id FROM orders WHERE id<4 UNION ALL SELECT id,customer_id FROM orders WHERE id>998) x JOIN customers c ON c.id=x.customer_id
```

## SQL-0007 · anti_exists

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM customers c WHERE NOT EXISTS(SELECT 1 FROM orders o WHERE o.customer_id=c.id)
```

## SQL-0008 · not_in_null

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id NOT IN (SELECT nullable_value FROM matrix_types)
```

## SQL-0009 · quantified_any

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE amount > ANY(SELECT price FROM items WHERE category=1)
```

## SQL-0010 · quantified_all

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE amount > ALL(SELECT price FROM items WHERE category=1)
```

## SQL-0011 · row_constructor

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE (customer_id,amount) IN ((1,17),(2,34))
```

## SQL-0012 · nested_subquery

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE amount>1000))
```

## SQL-0013 · cte_chain

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
WITH a AS (SELECT customer_id,amount FROM orders), b AS (SELECT customer_id,SUM(amount) total FROM a GROUP BY customer_id) SELECT * FROM b WHERE total>1000
```

## SQL-0014 · intersect

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<20 INTERSECT SELECT id FROM customers WHERE id>10
```

## SQL-0015 · except

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<20 EXCEPT SELECT id FROM customers WHERE id>10
```

## SQL-0016 · union_order_limit

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
(SELECT id FROM orders WHERE id<4) UNION ALL (SELECT id FROM customers WHERE id<4) ORDER BY id LIMIT 4
```

## SQL-0017 · distinct_aggregates

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(DISTINCT customer_id),AVG(amount),MIN(amount),MAX(amount),GROUP_CONCAT(DISTINCT customer_id ORDER BY customer_id) FROM orders GROUP BY status HAVING SUM(amount)>1000
```

## SQL-0018 · case_expression

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT CASE WHEN amount>100 THEN 'large' ELSE 'small' END AS size,COUNT(*) FROM orders GROUP BY size
```

## SQL-0019 · window_frame

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER(PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) FROM orders WHERE id<20
```

## SQL-0020 · window_rank_lag

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER(ORDER BY amount),DENSE_RANK() OVER(ORDER BY amount),LAG(amount,1,0) OVER(ORDER BY id) FROM orders WHERE id<20
```

## SQL-0021 · null_semantics

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE nullable_value IS NULL OR nullable_value <=> 2
```

## SQL-0022 · boolean_arithmetic

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,IF(flag,money,0),IFNULL(nullable_value,-1),COALESCE(nullable_value,signed_int,0),signed_int DIV 3,signed_int % 3 FROM matrix_types WHERE NOT flag XOR signed_int>10
```

## SQL-0023 · collation

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE ci_value='CASE-1' COLLATE utf8mb4_general_ci
```

## SQL-0024 · unicode_identifiers

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT `o`.`id` AS `中文列`, `o`.`note` AS `a``b` FROM `pcc_case_business`.`orders` AS `o` WHERE `o`.`id`=7
```

## SQL-0025 · comments_literals

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
/* ? ; */ SELECT id,'a;?b' AS txt FROM orders -- ; ?
 WHERE note LIKE '%100%' OR note='%s' # ; ?

```

## SQL-0026 · regexp_like

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE text_value REGEXP '^中文' OR text_value LIKE '中文%'
```

## SQL-0027 · partition_explicit

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM partitioned_orders PARTITION(p0,p1) WHERE id BETWEEN 90 AND 110
```

## SQL-0028 · tablesample

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders TABLESAMPLE REGIONS()
```

## SQL-0029 · update_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
UPDATE orders o JOIN customers c ON c.id=o.customer_id SET o.amount=o.amount+1 WHERE c.region=2 AND o.id<20
```

## SQL-0030 · update_subquery

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
UPDATE orders SET amount=(SELECT MAX(price) FROM items WHERE order_id=orders.id) WHERE id<3
```

## SQL-0031 · update_order_limit

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
UPDATE orders SET note='matrix' WHERE customer_id=1 ORDER BY id LIMIT 2
```

## SQL-0032 · delete_join

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
DELETE o FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=2 AND o.id<20
```

## SQL-0033 · delete_order_limit

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
DELETE FROM orders WHERE customer_id=1 ORDER BY id LIMIT 2
```

## SQL-0034 · insert_values

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT INTO orders(id,customer_id,amount,status,note) VALUES(20001,1,2,3,'matrix')
```

## SQL-0035 · insert_select

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT INTO orders(id,customer_id,amount,status,note) SELECT id+20000,customer_id,amount,status,note FROM orders WHERE id<3
```

## SQL-0036 · insert_upsert

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix') ON DUPLICATE KEY UPDATE amount=VALUES(amount)
```

## SQL-0037 · insert_ignore

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT IGNORE INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix')
```

## SQL-0038 · replace_values

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
REPLACE INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix')
```

## SQL-0039 · replace_select

类别：语法与 DML；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
REPLACE INTO orders(id,customer_id,amount,status,note) SELECT id+20000,customer_id,amount,status,note FROM orders WHERE id<3
```

## SQL-0040 · type_integers

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE tiny=-1 OR small=100 OR medium=1000 OR signed_int=1 OR signed_big=1000000 OR unsigned_big=9223372036854775809
```

## SQL-0041 · type_unsigned_max

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE unsigned_big=18446744073709551615
```

## SQL-0042 · type_decimal

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE money BETWEEN 1.123456 AND 9.654321
```

## SQL-0043 · type_decimal_65_30

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE precise_value=123456789012345678901234567890.123456789012345678901234567890
```

## SQL-0044 · type_float_double

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE f>1.2e0 AND d<2.0e0
```

## SQL-0045 · type_bit_bool

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE bits=b'00000001' OR flag=TRUE
```

## SQL-0046 · type_char_varchar

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE fixed_char='fixed' AND text_value LIKE '中文%'
```

## SQL-0047 · type_binary_varbinary

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE binary_value=X'6100620000000000' OR varbinary_value=0xff00
```

## SQL-0048 · type_text_family

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE tiny_text='tiny' AND text_body='body' AND medium_text='medium' AND long_text='long'
```

## SQL-0049 · type_blob_family

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE tiny_blob=X'74696e79' AND blob_value=X'ff00' AND medium_blob=X'6d656469756d' AND long_blob=X'6c6f6e67'
```

## SQL-0050 · type_date

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE day_value BETWEEN DATE '2024-02-28' AND DATE '2024-03-01'
```

## SQL-0051 · type_datetime_microsecond

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE dt='2024-02-29 12:34:56.123456'
```

## SQL-0052 · type_timestamp

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE ts>='2024-02-29 00:00:00.000001'
```

## SQL-0053 · type_time_year

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE duration='-12:34:56.123456' AND yr=2024
```

## SQL-0054 · type_enum_set

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE enum_value='paid' AND FIND_IN_SET('c',set_value)>0
```

## SQL-0055 · type_json_extract

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE JSON_EXTRACT(doc,'$.n')=3 AND doc->>'$.s'='?,;中文'
```

## SQL-0056 · type_generated_stored

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE generated_value=3
```

## SQL-0057 · type_generated_virtual

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE virtual_value=4
```

## SQL-0058 · type_implicit_cast

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE signed_int='7' AND money>'1.5'
```

## SQL-0059 · type_explicit_cast

类别：数据类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE CAST(text_value AS BINARY)=X'00' OR CAST(money AS CHAR) LIKE '1.%'
```

## SQL-0060 · hint_use_index

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>100
```

## SQL-0061 · hint_ignore_index

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ IGNORE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>100
```

## SQL-0062 · hint_index_merge

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX_MERGE(o,idx_amount,idx_customer) */ id FROM orders o WHERE amount=17 OR customer_id=3
```

## SQL-0063 · hint_no_index_merge

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ NO_INDEX_MERGE() */ id FROM orders o WHERE amount=17 OR customer_id=3
```

## SQL-0064 · hint_hash_join

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ HASH_JOIN(o,c) */ o.id FROM orders o JOIN customers c ON c.id=o.customer_id
```

## SQL-0065 · hint_merge_join

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ MERGE_JOIN(o,c) */ o.id FROM orders o JOIN customers c ON c.id=o.customer_id
```

## SQL-0066 · hint_inl_join

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ INL_JOIN(o) */ o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2
```

## SQL-0067 · hint_inl_hash_join

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ INL_HASH_JOIN(o) */ o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2
```

## SQL-0068 · hint_inl_merge_join

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ INL_MERGE_JOIN(o) */ o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2
```

## SQL-0069 · hint_leading

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ LEADING(c,o) */ o.id FROM orders o JOIN customers c ON c.id=o.customer_id
```

## SQL-0070 · hint_hash_agg

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ HASH_AGG() */ customer_id,SUM(amount) FROM orders GROUP BY customer_id
```

## SQL-0071 · hint_stream_agg

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ STREAM_AGG() */ customer_id,SUM(amount) FROM orders GROUP BY customer_id
```

## SQL-0072 · hint_agg_pushdown

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ AGG_TO_COP() */ customer_id,SUM(amount) FROM orders GROUP BY customer_id
```

## SQL-0073 · hint_limit_pushdown

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ LIMIT_TO_COP() */ id FROM orders LIMIT 4
```

## SQL-0074 · hint_read_tikv

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ READ_FROM_STORAGE(TIKV[o]) */ id FROM orders o WHERE amount>100
```

## SQL-0075 · hint_query_block

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ QB_NAME(main), USE_INDEX(@main o,idx_amount) */ id FROM orders o WHERE amount>100
```

## SQL-0076 · hint_multiple_case

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ use_index(o,idx_amount), MAX_EXECUTION_TIME(5000), MEMORY_QUOTA(64 MB) */ id FROM orders o WHERE amount>100
```

## SQL-0077 · hint_invalid_index

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(o,does_not_exist) */ id FROM orders o WHERE amount>100
```

## SQL-0078 · hint_unknown

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ PCC_UNKNOWN_HINT() */ id FROM orders o WHERE amount>100
```

## SQL-0079 · hint_update

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
UPDATE /*+ USE_INDEX(orders,PRIMARY) */ orders SET amount=1 WHERE id=7
```

## SQL-0080 · hint_delete

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
DELETE /*+ USE_INDEX(orders,PRIMARY) */ FROM orders WHERE id=7
```

## SQL-0081 · hint_insert

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT /*+ MEMORY_QUOTA(64 MB) */ INTO orders(id,customer_id,amount,status,note) VALUES(20001,1,2,3,'matrix')
```

## SQL-0082 · hint_subquery

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ NO_DECORRELATE() */ o.id,(SELECT /*+ USE_INDEX(i,idx_order) */ MAX(price) FROM items i WHERE i.order_id=o.id) FROM orders o WHERE o.id<5
```

## SQL-0083 · hint_cte

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
WITH c AS (SELECT /*+ USE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>100) SELECT * FROM c
```

## SQL-0084 · hint_misplaced

类别：优化器 Hint；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
/*+ USE_INDEX(orders,does_not_exist) */ SELECT id FROM orders WHERE id=7
```

## SQL-0085 · prepared_point_int

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "7"}]
```

## SQL-0086 · prepared_point_another_sample

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "999"}]
```

## SQL-0087 · prepared_point_string

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "string", "value": "7"}]
```

## SQL-0088 · prepared_null

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE nullable_value <=> ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "null", "value": null}]
```

## SQL-0089 · prepared_uint

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE unsigned_big=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "uint", "value": "18446744073709551615"}]
```

## SQL-0090 · prepared_decimal

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE precise_value=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "decimal", "value": "123456789012345678901234567890.123456789012345678901234567890"}]
```

## SQL-0091 · prepared_double

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE d>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "double", "value": "1.125e0"}]
```

## SQL-0092 · prepared_bool

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE flag=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "bool", "value": true}]
```

## SQL-0093 · prepared_bytes

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE varbinary_value=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "bytes", "value": "ff00"}]
```

## SQL-0094 · prepared_date

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE day_value=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "date", "value": "2024-02-29"}]
```

## SQL-0095 · prepared_datetime

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE dt=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "datetime", "value": "2024-02-29 12:34:56.123456"}]
```

## SQL-0096 · prepared_time

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE duration=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "time", "value": "-12:34:56.123456"}]
```

## SQL-0097 · prepared_json

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "json", "value": "{\"n\":3}"}]
```

## SQL-0098 · prepared_hostile_string

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE note=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "string", "value": "'; DELETE FROM orders; -- ?, %s 中文😀\\\n"}]
```

## SQL-0099 · prepared_like

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE text_value LIKE ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "string", "value": "中文%"}]
```

## SQL-0100 · prepared_limit_offset

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders ORDER BY id LIMIT ? OFFSET ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "5"}, {"type": "int", "value": "2"}]
```

## SQL-0101 · prepared_cte

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
WITH c AS (SELECT id FROM orders WHERE amount>?) SELECT * FROM c WHERE id<?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "100"}, {"type": "int", "value": "20"}]
```

## SQL-0102 · prepared_hint

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>? AND amount<?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "100"}, {"type": "int", "value": "500"}]
```

## SQL-0103 · prepared_update

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
UPDATE /*+ USE_INDEX(orders,PRIMARY) */ orders SET amount=? WHERE id=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "500"}, {"type": "int", "value": "7"}]
```

## SQL-0104 · prepared_delete

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
DELETE FROM orders WHERE id=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "7"}]
```

## SQL-0105 · prepared_insert

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
INSERT INTO orders(id,customer_id,amount,status,note) VALUES(?,?,?,?,?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "20001"}, {"type": "int", "value": "1"}, {"type": "int", "value": "2"}, {"type": "int", "value": "3"}, {"type": "string", "value": "matrix"}]
```

## SQL-0106 · prepared_replace

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
REPLACE INTO orders(id,customer_id,amount,status,note) VALUES(?,?,?,?,?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "7"}, {"type": "int", "value": "1"}, {"type": "int", "value": "2"}, {"type": "int", "value": "3"}, {"type": "string", "value": "matrix"}]
```

## SQL-0107 · prepared_zero_parameters

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=7
```
参数样例（依次对应 `?`）：

```json
[]
```

## SQL-0108 · prepared_question_literals

类别：Prepared 参数类型；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /* ? */ '?' AS text,id FROM orders WHERE id=? -- ?

```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "7"}]
```

## SQL-0109 · long_comment_4096

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(orders,PRIMARY) */ /*中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;*/ id FROM orders WHERE id=7
```

## SQL-0110 · long_comment_65536

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(orders,PRIMARY) */ /*中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文
-- 展示节选；完整 SQL 见同编号 JSONL 记录。
```
完整 SQL 为 65614 字节，文档仅显示开头；执行使用完整原文。

## SQL-0111 · long_comment_1048576

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT /*+ USE_INDEX(orders,PRIMARY) */ /*中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文?;中文
-- 展示节选；完整 SQL 见同编号 JSONL 记录。
```
完整 SQL 为 1048654 字节，文档仅显示开头；执行使用完整原文。

## SQL-0112 · long_in_20000

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id IN (0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99,100,101,102,103,104,105,106,107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,125,126,127,128,129,130,131,132,133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173,174,175,176,177,178,179,180,181,182,183,184,185,186,187,188,189,190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209,210,211,212,213,214,215,216,217,218,219,220,221,222,223,224,225,226,227,228,229,230,231,232,233,234,235,236,237,238,239,240,241,242,243,244,245,246,247,248,249,250,251,252,253,254,255,256,257,258,259,260,261,262,263,264,265,266,267,268
-- 展示节选；完整 SQL 见同编号 JSONL 记录。
```
完整 SQL 为 108925 字节，文档仅显示开头；执行使用完整原文。

## SQL-0113 · long_union_128

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=0 UNION ALL SELECT id FROM orders WHERE id=1 UNION ALL SELECT id FROM orders WHERE id=2 UNION ALL SELECT id FROM orders WHERE id=3 UNION ALL SELECT id FROM orders WHERE id=4 UNION ALL SELECT id FROM orders WHERE id=5 UNION ALL SELECT id FROM orders WHERE id=6 UNION ALL SELECT id FROM orders WHERE id=7 UNION ALL SELECT id FROM orders WHERE id=8 UNION ALL SELECT id FROM orders WHERE id=9 UNION ALL SELECT id FROM orders WHERE id=10 UNION ALL SELECT id FROM orders WHERE id=11 UNION ALL SELECT id FROM orders WHERE id=12 UNION ALL SELECT id FROM orders WHERE id=13 UNION ALL SELECT id FROM orders WHERE id=14 UNION ALL SELECT id FROM orders WHERE id=15 UNION ALL SELECT id FROM orders WHERE id=16 UNION ALL SELECT id FROM orders WHERE id=17 UNION ALL SELECT id FROM orders WHERE id=18 UNION ALL SELECT id FROM orders WHERE id=19 UNION ALL SELECT id FROM orders WHERE id=20 UNION ALL SELECT id FROM orders WHERE id=21 UNION ALL SELECT id FROM orders WHERE id=22 UNION AL
-- 展示节选；完整 SQL 见同编号 JSONL 记录。
```
完整 SQL 为 5639 字节，文档仅显示开头；执行使用完整原文。

## SQL-0114 · long_prepared_256

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id IN (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "0"}, {"type": "int", "value": "1"}, {"type": "int", "value": "2"}, {"type": "int", "value": "3"}, {"type": "int", "value": "4"}, {"type": "int", "value": "5"}, {"type": "int", "value": "6"}, {"type": "int", "value": "7"}, {"type": "int", "value": "8"}, {"type": "int", "value": "9"}, {"type": "int", "value": "10"}, {"type": "int", "value": "11"}, {"type": "int", "value": "12"}, {"type": "int", "value": "13"}, {"type": "int", "value": "14"}, {"type": "int", "value": "15"}, {"type": "int", "value": "16"}, {"type": "int", "value": "17"}, {"type": "int", "value": "18"}, {"type": "int", "value": "19"}, {"type": "int", "value": "20"}, {"type": "int", "value": "21"}, {"typ ...（完整参数见同编号记录）
```

## SQL-0115 · long_prepared_string

类别：超长 SQL 与参数；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM matrix_types WHERE text_value=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "string", "value": "中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,;'😀中文?,; ...（完整参数见同编号记录）
```

## SQL-0116 · filter_pk_eq_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id=7
```

## SQL-0117 · filter_pk_eq_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE id=7
```

## SQL-0118 · filter_pk_eq_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id=7 ORDER BY amount DESC LIMIT 20
```

## SQL-0119 · filter_pk_eq_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE id=7 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0120 · filter_pk_eq_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE id=7 GROUP BY status
```

## SQL-0121 · filter_pk_eq_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE id=7
```

## SQL-0122 · filter_pk_range_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id BETWEEN 10 AND 100
```

## SQL-0123 · filter_pk_range_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE id BETWEEN 10 AND 100
```

## SQL-0124 · filter_pk_range_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id BETWEEN 10 AND 100 ORDER BY amount DESC LIMIT 20
```

## SQL-0125 · filter_pk_range_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE id BETWEEN 10 AND 100 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0126 · filter_pk_range_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE id BETWEEN 10 AND 100 GROUP BY status
```

## SQL-0127 · filter_pk_range_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE id BETWEEN 10 AND 100
```

## SQL-0128 · filter_secondary_eq_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id=7
```

## SQL-0129 · filter_secondary_eq_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE customer_id=7
```

## SQL-0130 · filter_secondary_eq_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id=7 ORDER BY amount DESC LIMIT 20
```

## SQL-0131 · filter_secondary_eq_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE customer_id=7 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0132 · filter_secondary_eq_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE customer_id=7 GROUP BY status
```

## SQL-0133 · filter_secondary_eq_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE customer_id=7
```

## SQL-0134 · filter_secondary_range_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE amount>=100 AND amount<400
```

## SQL-0135 · filter_secondary_range_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE amount>=100 AND amount<400
```

## SQL-0136 · filter_secondary_range_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount>=100 AND amount<400 ORDER BY amount DESC LIMIT 20
```

## SQL-0137 · filter_secondary_range_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE amount>=100 AND amount<400 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0138 · filter_secondary_range_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE amount>=100 AND amount<400 GROUP BY status
```

## SQL-0139 · filter_secondary_range_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE amount>=100 AND amount<400
```

## SQL-0140 · filter_composite_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE status=1 AND amount>100
```

## SQL-0141 · filter_composite_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE status=1 AND amount>100
```

## SQL-0142 · filter_composite_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status=1 AND amount>100 ORDER BY amount DESC LIMIT 20
```

## SQL-0143 · filter_composite_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE status=1 AND amount>100 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0144 · filter_composite_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE status=1 AND amount>100 GROUP BY status
```

## SQL-0145 · filter_composite_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE status=1 AND amount>100
```

## SQL-0146 · filter_or_indexes_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE amount=37 OR customer_id=3
```

## SQL-0147 · filter_or_indexes_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE amount=37 OR customer_id=3
```

## SQL-0148 · filter_or_indexes_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount=37 OR customer_id=3 ORDER BY amount DESC LIMIT 20
```

## SQL-0149 · filter_or_indexes_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE amount=37 OR customer_id=3 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0150 · filter_or_indexes_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE amount=37 OR customer_id=3 GROUP BY status
```

## SQL-0151 · filter_or_indexes_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE amount=37 OR customer_id=3
```

## SQL-0152 · filter_in_list_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id IN (1,5,20)
```

## SQL-0153 · filter_in_list_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE customer_id IN (1,5,20)
```

## SQL-0154 · filter_in_list_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id IN (1,5,20) ORDER BY amount DESC LIMIT 20
```

## SQL-0155 · filter_in_list_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE customer_id IN (1,5,20) ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0156 · filter_in_list_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE customer_id IN (1,5,20) GROUP BY status
```

## SQL-0157 · filter_in_list_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE customer_id IN (1,5,20)
```

## SQL-0158 · filter_not_equal_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE status<>2
```

## SQL-0159 · filter_not_equal_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE status<>2
```

## SQL-0160 · filter_not_equal_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status<>2 ORDER BY amount DESC LIMIT 20
```

## SQL-0161 · filter_not_equal_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE status<>2 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0162 · filter_not_equal_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE status<>2 GROUP BY status
```

## SQL-0163 · filter_not_equal_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE status<>2
```

## SQL-0164 · filter_like_prefix_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE note LIKE 'item-1%'
```

## SQL-0165 · filter_like_prefix_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE note LIKE 'item-1%'
```

## SQL-0166 · filter_like_prefix_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE 'item-1%' ORDER BY amount DESC LIMIT 20
```

## SQL-0167 · filter_like_prefix_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE note LIKE 'item-1%' ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0168 · filter_like_prefix_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE note LIKE 'item-1%' GROUP BY status
```

## SQL-0169 · filter_like_prefix_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE note LIKE 'item-1%'
```

## SQL-0170 · filter_like_suffix_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE note LIKE '%100%'
```

## SQL-0171 · filter_like_suffix_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE note LIKE '%100%'
```

## SQL-0172 · filter_like_suffix_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE '%100%' ORDER BY amount DESC LIMIT 20
```

## SQL-0173 · filter_like_suffix_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE note LIKE '%100%' ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0174 · filter_like_suffix_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE note LIKE '%100%' GROUP BY status
```

## SQL-0175 · filter_like_suffix_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE note LIKE '%100%'
```

## SQL-0176 · filter_empty_range_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id>20 AND id<10
```

## SQL-0177 · filter_empty_range_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE id>20 AND id<10
```

## SQL-0178 · filter_empty_range_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id>20 AND id<10 ORDER BY amount DESC LIMIT 20
```

## SQL-0179 · filter_empty_range_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE id>20 AND id<10 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0180 · filter_empty_range_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE id>20 AND id<10 GROUP BY status
```

## SQL-0181 · filter_empty_range_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE id>20 AND id<10
```

## SQL-0182 · filter_expression_cover

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE amount+1>100
```

## SQL-0183 · filter_expression_lookup

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM orders WHERE amount+1>100
```

## SQL-0184 · filter_expression_sort

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount+1>100 ORDER BY amount DESC LIMIT 20
```

## SQL-0185 · filter_expression_offset

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,customer_id FROM orders WHERE amount+1>100 ORDER BY id LIMIT 10 OFFSET 100
```

## SQL-0186 · filter_expression_aggregate

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*),SUM(amount) FROM orders WHERE amount+1>100 GROUP BY status
```

## SQL-0187 · filter_expression_distinct

类别：谓词 × 投影 / 排序 / 聚合；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT DISTINCT customer_id FROM orders WHERE amount+1>100
```

## SQL-0188 · join_join_outer_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2
```

## SQL-0189 · join_join_outer_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0190 · join_join_outer_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY c.id,o.id
```

## SQL-0191 · join_join_inner_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.amount>100
```

## SQL-0192 · join_join_inner_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0193 · join_join_inner_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY c.id,o.id
```

## SQL-0194 · join_join_both_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1
```

## SQL-0195 · join_join_both_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0196 · join_join_both_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY c.id,o.id
```

## SQL-0197 · join_join_null_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL
```

## SQL-0198 · join_join_null_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY o.amount DESC LIMIT 15
```

## SQL-0199 · join_join_null_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY c.id,o.id
```

## SQL-0200 · join_join_or_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500
```

## SQL-0201 · join_join_or_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0202 · join_join_or_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY c.id,o.id
```

## SQL-0203 · join_left_join_outer_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2
```

## SQL-0204 · join_left_join_outer_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0205 · join_left_join_outer_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY c.id,o.id
```

## SQL-0206 · join_left_join_inner_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100
```

## SQL-0207 · join_left_join_inner_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0208 · join_left_join_inner_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY c.id,o.id
```

## SQL-0209 · join_left_join_both_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1
```

## SQL-0210 · join_left_join_both_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0211 · join_left_join_both_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY c.id,o.id
```

## SQL-0212 · join_left_join_null_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL
```

## SQL-0213 · join_left_join_null_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY o.amount DESC LIMIT 15
```

## SQL-0214 · join_left_join_null_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY c.id,o.id
```

## SQL-0215 · join_left_join_or_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500
```

## SQL-0216 · join_left_join_or_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0217 · join_left_join_or_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY c.id,o.id
```

## SQL-0218 · join_right_join_outer_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2
```

## SQL-0219 · join_right_join_outer_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0220 · join_right_join_outer_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 ORDER BY c.id,o.id
```

## SQL-0221 · join_right_join_inner_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100
```

## SQL-0222 · join_right_join_inner_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0223 · join_right_join_inner_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.amount>100 ORDER BY c.id,o.id
```

## SQL-0224 · join_right_join_both_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1
```

## SQL-0225 · join_right_join_both_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0226 · join_right_join_both_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 AND o.status=1 ORDER BY c.id,o.id
```

## SQL-0227 · join_right_join_null_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL
```

## SQL-0228 · join_right_join_null_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY o.amount DESC LIMIT 15
```

## SQL-0229 · join_right_join_null_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL ORDER BY c.id,o.id
```

## SQL-0230 · join_right_join_or_plain

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500
```

## SQL-0231 · join_right_join_or_topn

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY o.amount DESC LIMIT 15
```

## SQL-0232 · join_right_join_or_ordered

类别：连接类型 × 过滤位置 × 排序；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name,o.id,o.amount FROM customers c RIGHT JOIN orders o ON o.customer_id=c.id WHERE c.region=2 OR o.amount>500 ORDER BY c.id,o.id
```

## SQL-0233 · aggregate_232

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) AS result FROM orders GROUP BY status HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0234 · aggregate_233

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) AS result FROM orders WHERE amount>100 GROUP BY status HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0235 · aggregate_234

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0236 · aggregate_235

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,SUM(amount) AS result FROM orders GROUP BY status HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0237 · aggregate_236

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,SUM(amount) AS result FROM orders WHERE amount>100 GROUP BY status HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0238 · aggregate_237

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,SUM(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0239 · aggregate_238

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(DISTINCT customer_id) AS result FROM orders GROUP BY status HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0240 · aggregate_239

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(DISTINCT customer_id) AS result FROM orders WHERE amount>100 GROUP BY status HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0241 · aggregate_240

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(DISTINCT customer_id) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0242 · aggregate_241

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,AVG(amount) AS result FROM orders GROUP BY status HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0243 · aggregate_242

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,AVG(amount) AS result FROM orders WHERE amount>100 GROUP BY status HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0244 · aggregate_243

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,AVG(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0245 · aggregate_244

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(*) AS result FROM orders GROUP BY customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0246 · aggregate_245

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(*) AS result FROM orders WHERE amount>100 GROUP BY customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0247 · aggregate_246

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(*) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0248 · aggregate_247

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,SUM(amount) AS result FROM orders GROUP BY customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0249 · aggregate_248

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,SUM(amount) AS result FROM orders WHERE amount>100 GROUP BY customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0250 · aggregate_249

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,SUM(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0251 · aggregate_250

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(DISTINCT customer_id) AS result FROM orders GROUP BY customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0252 · aggregate_251

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(DISTINCT customer_id) AS result FROM orders WHERE amount>100 GROUP BY customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0253 · aggregate_252

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,COUNT(DISTINCT customer_id) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0254 · aggregate_253

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,AVG(amount) AS result FROM orders GROUP BY customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0255 · aggregate_254

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,AVG(amount) AS result FROM orders WHERE amount>100 GROUP BY customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0256 · aggregate_255

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT customer_id,AVG(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0257 · aggregate_256

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(*) AS result FROM orders GROUP BY status,customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0258 · aggregate_257

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(*) AS result FROM orders WHERE amount>100 GROUP BY status,customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0259 · aggregate_258

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(*) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status,customer_id HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0260 · aggregate_259

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,SUM(amount) AS result FROM orders GROUP BY status,customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0261 · aggregate_260

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,SUM(amount) AS result FROM orders WHERE amount>100 GROUP BY status,customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0262 · aggregate_261

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,SUM(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status,customer_id HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0263 · aggregate_262

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(DISTINCT customer_id) AS result FROM orders GROUP BY status,customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0264 · aggregate_263

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(DISTINCT customer_id) AS result FROM orders WHERE amount>100 GROUP BY status,customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0265 · aggregate_264

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,COUNT(DISTINCT customer_id) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status,customer_id HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0266 · aggregate_265

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,AVG(amount) AS result FROM orders GROUP BY status,customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0267 · aggregate_266

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,AVG(amount) AS result FROM orders WHERE amount>100 GROUP BY status,customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0268 · aggregate_267

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,customer_id,AVG(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY status,customer_id HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0269 · aggregate_268

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(*) AS result FROM orders GROUP BY MOD(customer_id,5) HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0270 · aggregate_269

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(*) AS result FROM orders WHERE amount>100 GROUP BY MOD(customer_id,5) HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0271 · aggregate_270

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(*) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY MOD(customer_id,5) HAVING COUNT(*)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0272 · aggregate_271

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),SUM(amount) AS result FROM orders GROUP BY MOD(customer_id,5) HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0273 · aggregate_272

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),SUM(amount) AS result FROM orders WHERE amount>100 GROUP BY MOD(customer_id,5) HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0274 · aggregate_273

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),SUM(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY MOD(customer_id,5) HAVING SUM(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0275 · aggregate_274

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(DISTINCT customer_id) AS result FROM orders GROUP BY MOD(customer_id,5) HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0276 · aggregate_275

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(DISTINCT customer_id) AS result FROM orders WHERE amount>100 GROUP BY MOD(customer_id,5) HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0277 · aggregate_276

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),COUNT(DISTINCT customer_id) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY MOD(customer_id,5) HAVING COUNT(DISTINCT customer_id)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0278 · aggregate_277

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),AVG(amount) AS result FROM orders GROUP BY MOD(customer_id,5) HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0279 · aggregate_278

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),AVG(amount) AS result FROM orders WHERE amount>100 GROUP BY MOD(customer_id,5) HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0280 · aggregate_279

类别：分组键 × 聚合函数 × 条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT MOD(customer_id,5),AVG(amount) AS result FROM orders WHERE status=1 AND amount<500 GROUP BY MOD(customer_id,5) HAVING AVG(amount)>2 ORDER BY result DESC LIMIT 10
```

## SQL-0281 · subquery_exists_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.amount>100)
```

## SQL-0282 · subquery_exists_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.amount>100) AND c.region=1
```

## SQL-0283 · subquery_exists_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.amount>100) ORDER BY c.id LIMIT 5
```

## SQL-0284 · subquery_not_exists_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.status=1)
```

## SQL-0285 · subquery_not_exists_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.status=1) AND c.region=1
```

## SQL-0286 · subquery_not_exists_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id AND o.status=1) ORDER BY c.id LIMIT 5
```

## SQL-0287 · subquery_in_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE amount>100)
```

## SQL-0288 · subquery_in_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE amount>100) AND c.region=1
```

## SQL-0289 · subquery_in_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE amount>100) ORDER BY c.id LIMIT 5
```

## SQL-0290 · subquery_not_in_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id NOT IN (SELECT customer_id FROM orders WHERE status=1)
```

## SQL-0291 · subquery_not_in_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id NOT IN (SELECT customer_id FROM orders WHERE status=1) AND c.region=1
```

## SQL-0292 · subquery_not_in_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id NOT IN (SELECT customer_id FROM orders WHERE status=1) ORDER BY c.id LIMIT 5
```

## SQL-0293 · subquery_scalar_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id < (SELECT MAX(customer_id) FROM orders WHERE status=2)
```

## SQL-0294 · subquery_scalar_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id < (SELECT MAX(customer_id) FROM orders WHERE status=2) AND c.region=1
```

## SQL-0295 · subquery_scalar_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id < (SELECT MAX(customer_id) FROM orders WHERE status=2) ORDER BY c.id LIMIT 5
```

## SQL-0296 · subquery_correlated_scalar_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE (SELECT SUM(amount) FROM orders o WHERE o.customer_id=c.id)>200
```

## SQL-0297 · subquery_correlated_scalar_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE (SELECT SUM(amount) FROM orders o WHERE o.customer_id=c.id)>200 AND c.region=1
```

## SQL-0298 · subquery_correlated_scalar_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE (SELECT SUM(amount) FROM orders o WHERE o.customer_id=c.id)>200 ORDER BY c.id LIMIT 5
```

## SQL-0299 · subquery_nested_none

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE id IN (SELECT order_id FROM items WHERE category=2))
```

## SQL-0300 · subquery_nested_region

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE id IN (SELECT order_id FROM items WHERE category=2)) AND c.region=1
```

## SQL-0301 · subquery_nested_limit

类别：子查询 × 外层条件；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT c.id,c.name FROM customers c WHERE c.id IN (SELECT customer_id FROM orders WHERE id IN (SELECT order_id FROM items WHERE category=2)) ORDER BY c.id LIMIT 5
```

## SQL-0302 · window_301

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0303 · window_302

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0304 · window_303

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY status ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0305 · window_304

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY status ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0306 · window_305

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY status,customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0307 · window_306

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER (PARTITION BY status,customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0308 · window_307

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0309 · window_308

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0310 · window_309

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY status ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0311 · window_310

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY status ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0312 · window_311

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY status,customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0313 · window_312

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,RANK() OVER (PARTITION BY status,customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0314 · window_313

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0315 · window_314

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0316 · window_315

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY status ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0317 · window_316

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY status ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0318 · window_317

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY status,customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0319 · window_318

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,SUM(amount) OVER (PARTITION BY status,customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0320 · window_319

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0321 · window_320

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0322 · window_321

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY status ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0323 · window_322

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY status ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0324 · window_323

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY status,customer_id ORDER BY id) AS value FROM orders WHERE id<100
```

## SQL-0325 · window_324

类别：窗口函数 × 分区 × 窗口范围；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,AVG(amount) OVER (PARTITION BY status,customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS value FROM orders WHERE id<100
```

## SQL-0326 · set_union_plain

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION SELECT id FROM customers WHERE region=1
```

## SQL-0327 · set_union_sort

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION SELECT id FROM customers WHERE region=1 ORDER BY id DESC
```

## SQL-0328 · set_union_topn

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION SELECT id FROM customers WHERE region=1 ORDER BY id LIMIT 10
```

## SQL-0329 · set_union_all_plain

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION ALL SELECT id FROM customers WHERE region=1
```

## SQL-0330 · set_union_all_sort

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION ALL SELECT id FROM customers WHERE region=1 ORDER BY id DESC
```

## SQL-0331 · set_union_all_topn

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 UNION ALL SELECT id FROM customers WHERE region=1 ORDER BY id LIMIT 10
```

## SQL-0332 · set_intersect_plain

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 INTERSECT SELECT id FROM customers WHERE region=1
```

## SQL-0333 · set_intersect_sort

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 INTERSECT SELECT id FROM customers WHERE region=1 ORDER BY id DESC
```

## SQL-0334 · set_intersect_topn

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 INTERSECT SELECT id FROM customers WHERE region=1 ORDER BY id LIMIT 10
```

## SQL-0335 · set_except_plain

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 EXCEPT SELECT id FROM customers WHERE region=1
```

## SQL-0336 · set_except_sort

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 EXCEPT SELECT id FROM customers WHERE region=1 ORDER BY id DESC
```

## SQL-0337 · set_except_topn

类别：集合操作 × 排序 / TopN；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE id<50 EXCEPT SELECT id FROM customers WHERE region=1 ORDER BY id LIMIT 10
```

## SQL-0338 · parameter_range_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount BETWEEN ? AND ? ORDER BY amount LIMIT 10
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "0"}, {"type": "int", "value": "10"}]
```

## SQL-0339 · parameter_range_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount BETWEEN ? AND ? ORDER BY amount LIMIT 10
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "100"}, {"type": "int", "value": "900"}]
```

## SQL-0340 · parameter_range_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount BETWEEN ? AND ? ORDER BY amount LIMIT 10
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "2001"}, {"type": "int", "value": "3000"}]
```

## SQL-0341 · parameter_join_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=? AND o.amount>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "0"}, {"type": "int", "value": "0"}]
```

## SQL-0342 · parameter_join_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=? AND o.amount>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "2"}, {"type": "int", "value": "500"}]
```

## SQL-0343 · parameter_join_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT o.id,c.name FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=? AND o.amount>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "9"}, {"type": "int", "value": "9999"}]
```

## SQL-0344 · parameter_aggregate_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) FROM orders WHERE amount>? GROUP BY status HAVING COUNT(*)>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "0"}, {"type": "int", "value": "1"}]
```

## SQL-0345 · parameter_aggregate_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) FROM orders WHERE amount>? GROUP BY status HAVING COUNT(*)>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "500"}, {"type": "int", "value": "20"}]
```

## SQL-0346 · parameter_aggregate_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT status,COUNT(*) FROM orders WHERE amount>? GROUP BY status HAVING COUNT(*)>?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "3000"}, {"type": "int", "value": "10"}]
```

## SQL-0347 · parameter_subquery_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status=? AND amount>?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "0"}, {"type": "int", "value": "10"}]
```

## SQL-0348 · parameter_subquery_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status=? AND amount>?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "1"}, {"type": "int", "value": "1000"}]
```

## SQL-0349 · parameter_subquery_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE status=? AND amount>?)
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "9"}, {"type": "int", "value": "2000"}]
```

## SQL-0350 · parameter_window_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER(PARTITION BY status ORDER BY amount) FROM orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "1"}, {"type": "int", "value": "10"}]
```

## SQL-0351 · parameter_window_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER(PARTITION BY status ORDER BY amount) FROM orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "100"}, {"type": "int", "value": "500"}]
```

## SQL-0352 · parameter_window_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,ROW_NUMBER() OVER(PARTITION BY status ORDER BY amount) FROM orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "2000"}, {"type": "int", "value": "3000"}]
```

## SQL-0353 · parameter_union_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id=? UNION ALL SELECT id FROM customers WHERE region=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "1"}, {"type": "int", "value": "0"}]
```

## SQL-0354 · parameter_union_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id=? UNION ALL SELECT id FROM customers WHERE region=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "90"}, {"type": "int", "value": "4"}]
```

## SQL-0355 · parameter_union_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM orders WHERE customer_id=? UNION ALL SELECT id FROM customers WHERE region=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "999"}, {"type": "int", "value": "8"}]
```

## SQL-0356 · parameter_partition_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM partitioned_orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "90"}, {"type": "int", "value": "110"}]
```

## SQL-0357 · parameter_partition_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM partitioned_orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "190"}, {"type": "int", "value": "210"}]
```

## SQL-0358 · parameter_partition_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id FROM partitioned_orders WHERE id BETWEEN ? AND ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "300"}, {"type": "int", "value": "400"}]
```

## SQL-0359 · parameter_limit_0

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders ORDER BY amount LIMIT ? OFFSET ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "1"}, {"type": "int", "value": "0"}]
```

## SQL-0360 · parameter_limit_1

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders ORDER BY amount LIMIT ? OFFSET ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "10"}, {"type": "int", "value": "100"}]
```

## SQL-0361 · parameter_limit_2

类别：参数化组合 × 参数分布；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,note FROM orders ORDER BY amount LIMIT ? OFFSET ?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "100"}, {"type": "int", "value": "1000"}]
```

## SQL-0362 · pcc_case_reporting_pk_eq

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id=7 ORDER BY id LIMIT 20
```

## SQL-0363 · pcc_case_reporting_pk_range

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id BETWEEN 10 AND 100 ORDER BY id LIMIT 20
```

## SQL-0364 · pcc_case_reporting_secondary_eq

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id=7 ORDER BY id LIMIT 20
```

## SQL-0365 · pcc_case_reporting_secondary_range

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount>=100 AND amount<400 ORDER BY id LIMIT 20
```

## SQL-0366 · pcc_case_reporting_composite

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status=1 AND amount>100 ORDER BY id LIMIT 20
```

## SQL-0367 · pcc_case_reporting_or_indexes

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount=37 OR customer_id=3 ORDER BY id LIMIT 20
```

## SQL-0368 · pcc_case_reporting_in_list

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id IN (1,5,20) ORDER BY id LIMIT 20
```

## SQL-0369 · pcc_case_reporting_not_equal

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status<>2 ORDER BY id LIMIT 20
```

## SQL-0370 · pcc_case_reporting_like_prefix

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE 'item-1%' ORDER BY id LIMIT 20
```

## SQL-0371 · pcc_case_reporting_like_suffix

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE '%100%' ORDER BY id LIMIT 20
```

## SQL-0372 · pcc_case_reporting_empty_range

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id>20 AND id<10 ORDER BY id LIMIT 20
```

## SQL-0373 · pcc_case_reporting_expression

类别：多库分组；默认库：`pcc_case_reporting`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount+1>100 ORDER BY id LIMIT 20
```

## SQL-0374 · pcc_case_archive_pk_eq

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id=7 ORDER BY id LIMIT 20
```

## SQL-0375 · pcc_case_archive_pk_range

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id BETWEEN 10 AND 100 ORDER BY id LIMIT 20
```

## SQL-0376 · pcc_case_archive_secondary_eq

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id=7 ORDER BY id LIMIT 20
```

## SQL-0377 · pcc_case_archive_secondary_range

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount>=100 AND amount<400 ORDER BY id LIMIT 20
```

## SQL-0378 · pcc_case_archive_composite

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status=1 AND amount>100 ORDER BY id LIMIT 20
```

## SQL-0379 · pcc_case_archive_or_indexes

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount=37 OR customer_id=3 ORDER BY id LIMIT 20
```

## SQL-0380 · pcc_case_archive_in_list

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE customer_id IN (1,5,20) ORDER BY id LIMIT 20
```

## SQL-0381 · pcc_case_archive_not_equal

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE status<>2 ORDER BY id LIMIT 20
```

## SQL-0382 · pcc_case_archive_like_prefix

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE 'item-1%' ORDER BY id LIMIT 20
```

## SQL-0383 · pcc_case_archive_like_suffix

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE note LIKE '%100%' ORDER BY id LIMIT 20
```

## SQL-0384 · pcc_case_archive_empty_range

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE id>20 AND id<10 ORDER BY id LIMIT 20
```

## SQL-0385 · pcc_case_archive_expression

类别：多库分组；默认库：`pcc_case_archive`；预期校验：`ok`。

```sql
SELECT id,note FROM orders WHERE amount+1>100 ORDER BY id LIMIT 20
```

## SQL-0386 · cross_385

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a JOIN pcc_case_reporting.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0387 · cross_386

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a JOIN pcc_case_archive.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0388 · cross_387

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a LEFT JOIN pcc_case_reporting.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0389 · cross_388

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a LEFT JOIN pcc_case_archive.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0390 · cross_389

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a RIGHT JOIN pcc_case_reporting.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0391 · cross_390

类别：跨库连接；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a.id,b.amount FROM pcc_case_business.orders a RIGHT JOIN pcc_case_archive.orders b ON a.id=b.id WHERE a.customer_id=3
```

## SQL-0392 · view_391

类别：视图与嵌套视图；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM order_view WHERE id<10
```

## SQL-0393 · view_392

类别：视图与嵌套视图；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM order_view ORDER BY amount DESC LIMIT 10
```

## SQL-0394 · view_393

类别：视图与嵌套视图；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM nested_view WHERE id<10
```

## SQL-0395 · view_394

类别：视图与嵌套视图；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT * FROM nested_view ORDER BY amount DESC LIMIT 10
```

## SQL-0396 · plan_union_4

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,amount FROM orders WHERE customer_id=0 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=1 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=2 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=3 AND amount>100
```

## SQL-0397 · plan_union_8

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,amount FROM orders WHERE customer_id=0 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=1 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=2 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=3 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=4 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=5 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=6 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=7 AND amount>100
```

## SQL-0398 · plan_union_16

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,amount FROM orders WHERE customer_id=0 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=1 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=2 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=3 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=4 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=5 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=6 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=7 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=8 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=9 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=10 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=11 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=12 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=13 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=14 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=15 AND amount>100
```

## SQL-0399 · plan_union_32

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT id,amount FROM orders WHERE customer_id=0 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=1 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=2 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=3 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=4 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=5 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=6 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=7 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=8 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=9 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=10 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=11 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=12 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=13 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=14 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=15 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=16 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=17 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=18 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=19 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=20 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=21 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=22 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=23 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=24 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=25 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=26 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=27 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=28 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=29 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=30 AND amount>100 UNION ALL SELECT id,amount FROM orders WHERE customer_id=31 AND amount>100
```

## SQL-0400 · plan_join_4

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a0.id FROM orders a0 LEFT JOIN orders a1 ON a1.customer_id=a0.id LEFT JOIN orders a2 ON a2.customer_id=a1.id LEFT JOIN orders a3 ON a3.customer_id=a2.id WHERE a0.id<5
```

## SQL-0401 · plan_join_8

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a0.id FROM orders a0 LEFT JOIN orders a1 ON a1.customer_id=a0.id LEFT JOIN orders a2 ON a2.customer_id=a1.id LEFT JOIN orders a3 ON a3.customer_id=a2.id LEFT JOIN orders a4 ON a4.customer_id=a3.id LEFT JOIN orders a5 ON a5.customer_id=a4.id LEFT JOIN orders a6 ON a6.customer_id=a5.id LEFT JOIN orders a7 ON a7.customer_id=a6.id WHERE a0.id<5
```

## SQL-0402 · plan_join_12

类别：长计划：宽树与深树；默认库：`pcc_case_business`；预期校验：`ok`。

```sql
SELECT a0.id FROM orders a0 LEFT JOIN orders a1 ON a1.customer_id=a0.id LEFT JOIN orders a2 ON a2.customer_id=a1.id LEFT JOIN orders a3 ON a3.customer_id=a2.id LEFT JOIN orders a4 ON a4.customer_id=a3.id LEFT JOIN orders a5 ON a5.customer_id=a4.id LEFT JOIN orders a6 ON a6.customer_id=a5.id LEFT JOIN orders a7 ON a7.customer_id=a6.id LEFT JOIN orders a8 ON a8.customer_id=a7.id LEFT JOIN orders a9 ON a9.customer_id=a8.id LEFT JOIN orders a10 ON a10.customer_id=a9.id LEFT JOIN orders a11 ON a11.customer_id=a10.id WHERE a0.id<5
```

## SQL-0403 · missing_parameters

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
SELECT id FROM orders WHERE id=?
```

## SQL-0404 · too_few_parameters

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
SELECT id FROM orders WHERE id=? AND status=?
```
参数样例（依次对应 `?`）：

```json
[{"type": "int", "value": "1"}]
```

## SQL-0405 · execute_only

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
EXECUTE stmt USING @id
```

## SQL-0406 · prepare_only

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
PREPARE stmt FROM 'SELECT id FROM orders WHERE id=?'
```

## SQL-0407 · session_variable

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
SELECT id FROM orders WHERE id=@id
```

## SQL-0408 · truncated_literal

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`needs_sample`（`NEEDS_EXECUTABLE_SAMPLE`）。

```sql
SELECT id FROM orders WHERE note='missing
```

## SQL-0409 · multiple_statements

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`unsupported`（`SQL_REJECTED`）。

```sql
SELECT id FROM orders; SELECT id FROM customers
```

## SQL-0410 · locking

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`unsupported`（`SQL_REJECTED`）。

```sql
SELECT id FROM orders FOR UPDATE
```

## SQL-0411 · blocking_function

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`unsupported`（`SQL_REJECTED`）。

```sql
SELECT SLEEP(1) FROM orders
```

## SQL-0412 · executable_comment

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`unsupported`（`SQL_UNSUPPORTED`）。

```sql
SELECT /*!80000 SQL_NO_CACHE */ id FROM orders
```

## SQL-0413 · session_hint

类别：不应执行的边界反例；默认库：`pcc_case_business`；预期校验：`unsupported`（`SQL_REJECTED`）。

```sql
SELECT /*+ SET_VAR(tidb_enable_index_merge=1) */ id FROM orders
```
