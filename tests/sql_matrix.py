"""Named, reusable SQL coverage cases; version labels are targets, not certification."""
SCHEMA = "pcc_validation_20261011"
ALLOWED = ("select", "update", "delete", "insert", "replace")


def cases():
    result = []

    def add(name, category, sql, parameters=None, versions="6.5,8.5"):
        result.append(dict(name=name, category=category, sql_text=sql, schema=SCHEMA,
                           parameters=parameters, target_versions=versions))

    syntax = {
        "left_join": "SELECT o.id,c.name FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE o.id<20",
        "right_join": "SELECT o.id,c.name FROM orders o RIGHT JOIN customers c ON c.id=o.customer_id WHERE c.id<20",
        "cross_join": "SELECT o.id,c.id FROM orders o CROSS JOIN customers c WHERE o.id<3 AND c.id<3",
        "straight_join": "SELECT o.id,c.name FROM orders o STRAIGHT_JOIN customers c ON c.id=o.customer_id WHERE o.id<20",
        "join_using": "SELECT id FROM orders JOIN customers USING(id) WHERE id<20",
        "derived_union": "SELECT x.id,c.name FROM (SELECT id,customer_id FROM orders WHERE id<4 UNION ALL SELECT id,customer_id FROM orders WHERE id>998) x JOIN customers c ON c.id=x.customer_id",
        "anti_exists": "SELECT id FROM customers c WHERE NOT EXISTS(SELECT 1 FROM orders o WHERE o.customer_id=c.id)",
        "not_in_null": "SELECT id FROM orders WHERE customer_id NOT IN (SELECT nullable_value FROM matrix_types)",
        "quantified_any": "SELECT id FROM orders WHERE amount > ANY(SELECT price FROM items WHERE category=1)",
        "quantified_all": "SELECT id FROM orders WHERE amount > ALL(SELECT price FROM items WHERE category=1)",
        "row_constructor": "SELECT id FROM orders WHERE (customer_id,amount) IN ((1,17),(2,34))",
        "nested_subquery": "SELECT id FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE id IN (SELECT customer_id FROM orders WHERE amount>1000))",
        "cte_chain": "WITH a AS (SELECT customer_id,amount FROM orders), b AS (SELECT customer_id,SUM(amount) total FROM a GROUP BY customer_id) SELECT * FROM b WHERE total>1000",
        "intersect": "SELECT id FROM orders WHERE id<20 INTERSECT SELECT id FROM customers WHERE id>10",
        "except": "SELECT id FROM orders WHERE id<20 EXCEPT SELECT id FROM customers WHERE id>10",
        "union_order_limit": "(SELECT id FROM orders WHERE id<4) UNION ALL (SELECT id FROM customers WHERE id<4) ORDER BY id LIMIT 4",
        "distinct_aggregates": "SELECT status,COUNT(DISTINCT customer_id),AVG(amount),MIN(amount),MAX(amount),GROUP_CONCAT(DISTINCT customer_id ORDER BY customer_id) FROM orders GROUP BY status HAVING SUM(amount)>1000",
        "case_expression": "SELECT CASE WHEN amount>100 THEN 'large' ELSE 'small' END AS size,COUNT(*) FROM orders GROUP BY size",
        "window_frame": "SELECT id,SUM(amount) OVER(PARTITION BY customer_id ORDER BY id ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) FROM orders WHERE id<20",
        "window_rank_lag": "SELECT id,RANK() OVER(ORDER BY amount),DENSE_RANK() OVER(ORDER BY amount),LAG(amount,1,0) OVER(ORDER BY id) FROM orders WHERE id<20",
        "null_semantics": "SELECT id FROM matrix_types WHERE nullable_value IS NULL OR nullable_value <=> 2",
        "boolean_arithmetic": "SELECT id,IF(flag,money,0),IFNULL(nullable_value,-1),COALESCE(nullable_value,signed_int,0),signed_int DIV 3,signed_int % 3 FROM matrix_types WHERE NOT flag XOR signed_int>10",
        "collation": "SELECT id FROM matrix_types WHERE ci_value='CASE-1' COLLATE utf8mb4_general_ci",
        "unicode_identifiers": "SELECT `o`.`id` AS `中文列`, `o`.`note` AS `a``b` FROM `pcc_validation_20261011`.`orders` AS `o` WHERE `o`.`id`=7",
        "comments_literals": "/* ? ; */ SELECT id,'a;?b' AS txt FROM orders -- ; ?\n WHERE note LIKE '%100%' OR note='%s' # ; ?\n",
        "regexp_like": "SELECT id FROM matrix_types WHERE text_value REGEXP '^中文' OR text_value LIKE '中文%'",
        "partition_explicit": "SELECT * FROM partitioned_orders PARTITION(p0,p1) WHERE id BETWEEN 90 AND 110",
        "tablesample": "SELECT id FROM orders TABLESAMPLE REGIONS()",
        "update_join": "UPDATE orders o JOIN customers c ON c.id=o.customer_id SET o.amount=o.amount+1 WHERE c.region=2 AND o.id<20",
        "update_subquery": "UPDATE orders SET amount=(SELECT MAX(price) FROM items WHERE order_id=orders.id) WHERE id<3",
        "update_order_limit": "UPDATE orders SET note='matrix' WHERE customer_id=1 ORDER BY id LIMIT 2",
        "delete_join": "DELETE o FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=2 AND o.id<20",
        "delete_order_limit": "DELETE FROM orders WHERE customer_id=1 ORDER BY id LIMIT 2",
        "insert_values": "INSERT INTO orders(id,customer_id,amount,status,note) VALUES(20001,1,2,3,'matrix')",
        "insert_select": "INSERT INTO orders(id,customer_id,amount,status,note) SELECT id+20000,customer_id,amount,status,note FROM orders WHERE id<3",
        "insert_upsert": "INSERT INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix') ON DUPLICATE KEY UPDATE amount=VALUES(amount)",
        "insert_ignore": "INSERT IGNORE INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix')",
        "replace_values": "REPLACE INTO orders(id,customer_id,amount,status,note) VALUES(1,1,2,3,'matrix')",
        "replace_select": "REPLACE INTO orders(id,customer_id,amount,status,note) SELECT id+20000,customer_id,amount,status,note FROM orders WHERE id<3",
    }
    for name, sql in syntax.items():
        add(name, "syntax", sql)

    predicates = {
        "integers": "tiny=-1 OR small=100 OR medium=1000 OR signed_int=1 OR signed_big=1000000 OR unsigned_big=9223372036854775809",
        "unsigned_max": "unsigned_big=18446744073709551615",
        "decimal": "money BETWEEN 1.123456 AND 9.654321",
        "decimal_65_30": "precise_value=123456789012345678901234567890.123456789012345678901234567890",
        "float_double": "f>1.2e0 AND d<2.0e0",
        "bit_bool": "bits=b'00000001' OR flag=TRUE",
        "char_varchar": "fixed_char='fixed' AND text_value LIKE '中文%'",
        "binary_varbinary": "binary_value=X'6100620000000000' OR varbinary_value=0xff00",
        "text_family": "tiny_text='tiny' AND text_body='body' AND medium_text='medium' AND long_text='long'",
        "blob_family": "tiny_blob=X'74696e79' AND blob_value=X'ff00' AND medium_blob=X'6d656469756d' AND long_blob=X'6c6f6e67'",
        "date": "day_value BETWEEN DATE '2024-02-28' AND DATE '2024-03-01'",
        "datetime_microsecond": "dt='2024-02-29 12:34:56.123456'",
        "timestamp": "ts>='2024-02-29 00:00:00.000001'",
        "time_year": "duration='-12:34:56.123456' AND yr=2024",
        "enum_set": "enum_value='paid' AND FIND_IN_SET('c',set_value)>0",
        "json_extract": "JSON_EXTRACT(doc,'$.n')=3 AND doc->>'$.s'='?,;中文'",
        "generated_stored": "generated_value=3",
        "generated_virtual": "virtual_value=4",
        "implicit_cast": "signed_int='7' AND money>'1.5'",
        "explicit_cast": "CAST(text_value AS BINARY)=X'00' OR CAST(money AS CHAR) LIKE '1.%'",
    }
    for name, predicate in predicates.items():
        add("type_" + name, "data_types", "SELECT id FROM matrix_types WHERE " + predicate)

    hints = {
        "use_index": ("USE_INDEX(o,idx_amount)", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "ignore_index": ("IGNORE_INDEX(o,idx_amount)", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "index_merge": ("USE_INDEX_MERGE(o,idx_amount,idx_customer)", "SELECT {hint} id FROM orders o WHERE amount=17 OR customer_id=3"),
        "no_index_merge": ("NO_INDEX_MERGE()", "SELECT {hint} id FROM orders o WHERE amount=17 OR customer_id=3"),
        "hash_join": ("HASH_JOIN(o,c)", "SELECT {hint} o.id FROM orders o JOIN customers c ON c.id=o.customer_id"),
        "merge_join": ("MERGE_JOIN(o,c)", "SELECT {hint} o.id FROM orders o JOIN customers c ON c.id=o.customer_id"),
        "inl_join": ("INL_JOIN(o)", "SELECT {hint} o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2"),
        "inl_hash_join": ("INL_HASH_JOIN(o)", "SELECT {hint} o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2"),
        "inl_merge_join": ("INL_MERGE_JOIN(o)", "SELECT {hint} o.id FROM customers c JOIN orders o ON c.id=o.customer_id WHERE c.region=2"),
        "leading": ("LEADING(c,o)", "SELECT {hint} o.id FROM orders o JOIN customers c ON c.id=o.customer_id"),
        "hash_agg": ("HASH_AGG()", "SELECT {hint} customer_id,SUM(amount) FROM orders GROUP BY customer_id"),
        "stream_agg": ("STREAM_AGG()", "SELECT {hint} customer_id,SUM(amount) FROM orders GROUP BY customer_id"),
        "agg_pushdown": ("AGG_TO_COP()", "SELECT {hint} customer_id,SUM(amount) FROM orders GROUP BY customer_id"),
        "limit_pushdown": ("LIMIT_TO_COP()", "SELECT {hint} id FROM orders LIMIT 4"),
        "read_tikv": ("READ_FROM_STORAGE(TIKV[o])", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "query_block": ("QB_NAME(main), USE_INDEX(@main o,idx_amount)", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "multiple_case": ("use_index(o,idx_amount), MAX_EXECUTION_TIME(5000), MEMORY_QUOTA(64 MB)", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "invalid_index": ("USE_INDEX(o,does_not_exist)", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "unknown": ("PCC_UNKNOWN_HINT()", "SELECT {hint} id FROM orders o WHERE amount>100"),
        "update": ("USE_INDEX(orders,PRIMARY)", "UPDATE {hint} orders SET amount=1 WHERE id=7"),
        "delete": ("USE_INDEX(orders,PRIMARY)", "DELETE {hint} FROM orders WHERE id=7"),
        "insert": ("MEMORY_QUOTA(64 MB)", "INSERT {hint} INTO orders(id,customer_id,amount,status,note) VALUES(20001,1,2,3,'matrix')"),
    }
    for name, (hint, template) in hints.items():
        add("hint_" + name, "hints", template.format(hint="/*+ " + hint + " */"))
    add("hint_subquery", "hints", "SELECT /*+ NO_DECORRELATE() */ o.id,(SELECT /*+ USE_INDEX(i,idx_order) */ MAX(price) FROM items i WHERE i.order_id=o.id) FROM orders o WHERE o.id<5")
    add("hint_cte", "hints", "WITH c AS (SELECT /*+ USE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>100) SELECT * FROM c")
    add("hint_misplaced", "hints", "/*+ USE_INDEX(orders,does_not_exist) */ SELECT id FROM orders WHERE id=7")

    prepared = [
        ("point_int", "SELECT id FROM orders WHERE id=?", [("int","7")]),
        ("point_another_sample", "SELECT id FROM orders WHERE id=?", [("int","999")]),
        ("point_string", "SELECT id FROM orders WHERE id=?", [("string","7")]),
        ("null", "SELECT id FROM matrix_types WHERE nullable_value <=> ?", [("null",None)]),
        ("uint", "SELECT id FROM matrix_types WHERE unsigned_big=?", [("uint","18446744073709551615")]),
        ("decimal", "SELECT id FROM matrix_types WHERE precise_value=?", [("decimal","123456789012345678901234567890.123456789012345678901234567890")]),
        ("double", "SELECT id FROM matrix_types WHERE d>?", [("double","1.125e0")]),
        ("bool", "SELECT id FROM matrix_types WHERE flag=?", [("bool",True)]),
        ("bytes", "SELECT id FROM matrix_types WHERE varbinary_value=?", [("bytes","ff00")]),
        ("date", "SELECT id FROM matrix_types WHERE day_value=?", [("date","2024-02-29")]),
        ("datetime", "SELECT id FROM matrix_types WHERE dt=?", [("datetime","2024-02-29 12:34:56.123456")]),
        ("time", "SELECT id FROM matrix_types WHERE duration=?", [("time","-12:34:56.123456")]),
        ("json", "SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)", [("json",'{"n":3}')]),
        ("hostile_string", "SELECT id FROM orders WHERE note=?", [("string","'; DELETE FROM orders; -- ?, %s 中文😀\\\n")]),
        ("like", "SELECT id FROM matrix_types WHERE text_value LIKE ?", [("string","中文%")]),
        ("limit_offset", "SELECT id FROM orders ORDER BY id LIMIT ? OFFSET ?", [("int","5"),("int","2")]),
        ("cte", "WITH c AS (SELECT id FROM orders WHERE amount>?) SELECT * FROM c WHERE id<?", [("int","100"),("int","20")]),
        ("hint", "SELECT /*+ USE_INDEX(o,idx_amount) */ id FROM orders o WHERE amount>? AND amount<?", [("int","100"),("int","500")]),
        ("update", "UPDATE /*+ USE_INDEX(orders,PRIMARY) */ orders SET amount=? WHERE id=?", [("int","500"),("int","7")]),
        ("delete", "DELETE FROM orders WHERE id=?", [("int","7")]),
        ("insert", "INSERT INTO orders(id,customer_id,amount,status,note) VALUES(?,?,?,?,?)", [("int","20001"),("int","1"),("int","2"),("int","3"),("string","matrix")]),
        ("replace", "REPLACE INTO orders(id,customer_id,amount,status,note) VALUES(?,?,?,?,?)", [("int","7"),("int","1"),("int","2"),("int","3"),("string","matrix")]),
        ("zero_parameters", "SELECT id FROM orders WHERE id=7", []),
        ("question_literals", "SELECT /* ? */ '?' AS text,id FROM orders WHERE id=? -- ?\n", [("int","7")]),
    ]
    for name, sql, params in prepared:
        add("prepared_" + name, "prepared", sql, [{"type": k,"value": v} for k,v in params])
    for length in (4096, 65536, 1048576):
        add(f"long_comment_{length}", "long_sql", "SELECT /*+ USE_INDEX(orders,PRIMARY) */ /*" + "中文?;" * ((length // len("中文?;".encode())) + 1) + "*/ id FROM orders WHERE id=7")
    add("long_in_20000", "long_sql", "SELECT id FROM orders WHERE id IN (" + ",".join(str(n) for n in range(20000)) + ")")
    add("long_union_128", "long_sql", " UNION ALL ".join(f"SELECT id FROM orders WHERE id={i}" for i in range(128)))
    add("long_prepared_256", "long_sql", "SELECT id FROM orders WHERE id IN (" + ",".join("?" for _ in range(256)) + ")", [{"type":"int","value":str(i)} for i in range(256)])
    add("long_prepared_string", "long_sql", "SELECT id FROM matrix_types WHERE text_value=?", [{"type":"string","value":"中文?,;'😀" * 8192}])
    return result


CASES = cases()
