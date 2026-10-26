"""Additional SQL, parameter-boundary and table-layout acceptance cases."""
import os
from pathlib import Path

import pytest

from sql_matrix import ALLOWED, SCHEMA
from pcc_tool.artifacts.store import write_json
from pcc_tool.capture.connection import Database, load_profiles
from pcc_tool.capture.explain import capture_plan
from pcc_tool.collector.summary import make_query
from pcc_tool.plan.parser import parse_plan

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("PCC_RUN_SCENARIOS") != "1", reason="Extra live scenarios are explicitly opt-in")]

SQLS = {
    "natural_join": "SELECT id FROM orders NATURAL JOIN customers WHERE id<5",
    "join_derived_aggregate": "SELECT c.id,a.total FROM customers c LEFT JOIN (SELECT customer_id,SUM(amount) total FROM orders GROUP BY customer_id) a ON c.id=a.customer_id WHERE c.id<5",
    "anti_join_null": "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id=c.id WHERE o.id IS NULL",
    "scalar_subquery": "SELECT id FROM orders WHERE amount=(SELECT MAX(amount) FROM orders)",
    "subquery_case": "SELECT id,CASE WHEN EXISTS(SELECT 1 FROM items i WHERE i.order_id=o.id) THEN 1 ELSE 0 END FROM orders o WHERE id<5",
    "cte_window": "WITH c AS (SELECT id,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY id) rn FROM orders) SELECT id FROM c WHERE rn=1",
    "window_named": "SELECT id,FIRST_VALUE(amount) OVER w,LAST_VALUE(amount) OVER w FROM orders WINDOW w AS (PARTITION BY customer_id ORDER BY id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
    "window_ntile": "SELECT id,NTILE(4) OVER(ORDER BY amount),LEAD(amount) OVER(ORDER BY id) FROM orders WHERE id<20",
    "union_mixed": "SELECT id FROM orders WHERE id<3 UNION ALL SELECT id FROM customers WHERE id<3 UNION SELECT id FROM matrix_types WHERE id<3",
    "select_no_table": "SELECT 1,NULL,TRUE,CAST(1.125 AS DECIMAL(10,3)),JSON_OBJECT('a',1)",
    "empty_result": "SELECT * FROM orders WHERE 1=0",
    "null_in": "SELECT id FROM orders WHERE id IN (NULL,7) OR amount NOT IN (NULL,2)",
    "row_comparison": "SELECT id FROM orders WHERE (customer_id,amount)>(3,100)",
    "between_not": "SELECT id FROM orders WHERE amount NOT BETWEEN 10 AND 100",
    "strings": "SELECT id,CONCAT_WS('-',fixed_char,LEFT(text_value,3)),SUBSTRING(text_value,2,4),LENGTH(text_value),CHAR_LENGTH(text_value),HEX(varbinary_value) FROM matrix_types",
    "dates": "SELECT id,DATE_ADD(day_value,INTERVAL 1 DAY),TIMESTAMPDIFF(SECOND,dt,ts),DATE_FORMAT(dt,'%Y-%m-%d'),EXTRACT(YEAR FROM day_value) FROM matrix_types",
    "numeric_functions": "SELECT id,ABS(signed_int),ROUND(money,2),CEIL(money),FLOOR(money),POWER(signed_int,2),MOD(signed_int,3) FROM matrix_types",
    "json_functions": "SELECT id,JSON_ARRAY(1,2),JSON_OBJECT('n',signed_int),JSON_SET(doc,'$.x',1),JSON_REMOVE(doc,'$.s'),JSON_LENGTH(doc),JSON_UNQUOTE(JSON_EXTRACT(doc,'$.s')) FROM matrix_types",
    "charset_convert": "SELECT id FROM matrix_types WHERE CONVERT(text_value USING utf8mb4) COLLATE utf8mb4_bin LIKE '中文%'",
    "binary_comparison": "SELECT id FROM matrix_types WHERE BINARY ci_value=BINARY 'Case-1'",
    "insert_set": "INSERT INTO orders SET id=20002,customer_id=1,amount=2,status=3,note='case'",
    "insert_multi_values": "INSERT INTO orders(id,customer_id,amount,status,note) VALUES(20002,1,2,3,'a'),(20003,1,2,3,'b')",
    "insert_select_upsert": "INSERT INTO orders(id,customer_id,amount,status,note) SELECT id,customer_id,amount,status,note FROM orders WHERE id<3 ON DUPLICATE KEY UPDATE amount=VALUES(amount)",
    "update_ignore": "UPDATE IGNORE orders SET amount=amount+1 WHERE id=7",
    "update_multi_table": "UPDATE orders o JOIN customers c ON o.customer_id=c.id SET o.amount=1,c.region=2 WHERE o.id=7",
    "delete_using": "DELETE FROM o USING orders o JOIN customers c ON c.id=o.customer_id WHERE o.id=7",
    "delete_multi_table": "DELETE o,i FROM orders o JOIN items i ON i.order_id=o.id WHERE o.id=7",
    "replace_set": "REPLACE INTO orders SET id=7,customer_id=1,amount=2,status=3,note='case'",
    "trailing_comments": "SELECT id FROM orders WHERE id=7; -- tail ? ;\n",
}
PARAMETERS = {
    "int_min": ("SELECT id FROM matrix_types WHERE signed_big=?", [("int", "-9223372036854775808")]),
    "int_max": ("SELECT id FROM matrix_types WHERE signed_big=?", [("int", "9223372036854775807")]),
    "uint_zero": ("SELECT id FROM matrix_types WHERE unsigned_big=?", [("uint", "0")]),
    "decimal_negative": ("SELECT id FROM matrix_types WHERE money>?", [("decimal", "-999999999999.123456")]),
    "double_negative": ("SELECT id FROM matrix_types WHERE d>?", [("double", "-1.125e100")]),
    "empty_string": ("SELECT id FROM matrix_types WHERE text_value=?", [("string", "")]),
    "empty_bytes": ("SELECT id FROM matrix_types WHERE varbinary_value=?", [("bytes", "")]),
    "bool_false": ("SELECT id FROM matrix_types WHERE flag=?", [("bool", False)]),
    "time_positive": ("SELECT id FROM matrix_types WHERE duration=?", [("time", "12:34:56.123456")]),
    "time_over_day": ("SELECT id FROM matrix_types WHERE duration=?", [("time", "838:59:59")]),
    "time_zero": ("SELECT id FROM matrix_types WHERE duration=?", [("time", "00:00:00")]),
    "json_text": ("SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)", [("string", '{"n":3}')]),
    "json_array": ("SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)", [("json", '[1,null,true]')]),
    "json_null": ("SELECT id FROM matrix_types WHERE JSON_CONTAINS(doc,?)", [("json", 'null')]),
    "join": ("SELECT o.id FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region=? AND o.amount>?", [("int", "2"),("decimal", "1.25")]),
    "range": ("SELECT id FROM orders WHERE id BETWEEN ? AND ?", [("int", "2"),("int", "20")]),
    "in_one": ("SELECT id FROM orders WHERE id IN (?)", [("int", "7")]),
    "in_two": ("SELECT id FROM orders WHERE id IN (?,?)", [("int", "7"),("int", "9")]),
    "update_limit": ("UPDATE orders SET amount=? WHERE status=? ORDER BY id LIMIT ?", [("int", "5"),("int", "1"),("int", "2")]),
    "delete_limit": ("DELETE FROM orders WHERE status=? ORDER BY id LIMIT ?", [("int", "1"),("int", "2")]),
}
LAYOUTS = {
    "common_handle": "(`id` INT, `v` INT, `s` VARCHAR(64), PRIMARY KEY(id,v) CLUSTERED, KEY idx_v(v))",
    "nonclustered": "(`id` INT PRIMARY KEY NONCLUSTERED, `v` INT, `s` VARCHAR(64), KEY idx_v(v))",
    "heap": "(`id` INT, `v` INT, `s` VARCHAR(64), KEY idx_v(v))",
    "prefix": "(`id` INT PRIMARY KEY, `v` INT, `s` VARCHAR(64), KEY idx_s(s(8)))",
    "invisible": "(`id` INT PRIMARY KEY, `v` INT, `s` VARCHAR(64), KEY idx_v(v) INVISIBLE)",
    "hash_partition": "(`id` INT PRIMARY KEY, `v` INT, `s` VARCHAR(64), KEY idx_v(v)) PARTITION BY HASH(id) PARTITIONS 3",
    "range_columns": "(`id` INT PRIMARY KEY, `v` INT, `s` VARCHAR(64), KEY idx_v(v)) PARTITION BY RANGE COLUMNS(id) (PARTITION p0 VALUES LESS THAN(5),PARTITION p1 VALUES LESS THAN(MAXVALUE))",
}
REJECTED = {
    "create": "CREATE TABLE x(id INT)", "alter": "ALTER TABLE orders ADD c INT",
    "drop": "DROP TABLE orders", "truncate": "TRUNCATE TABLE orders",
    "show": "SHOW CREATE TABLE orders", "describe": "DESC orders",
    "begin": "BEGIN", "commit": "COMMIT", "rollback": "ROLLBACK",
    "grant": "GRANT SELECT ON *.* TO 'x'", "set": "SET @x=1",
    "analyze": "ANALYZE TABLE orders", "explain_analyze": "EXPLAIN ANALYZE SELECT * FROM orders",
    "outfile": "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    "lock": "SELECT * FROM orders FOR UPDATE", "load": "LOAD DATA LOCAL INFILE '/tmp/x' INTO TABLE orders",
}


@pytest.fixture(scope="module")
def live():
    profiles = load_profiles(os.environ["PCC_TEST_CONFIG"])
    root = Path(os.environ["PCC_LIVE_OUTPUT"])
    root.mkdir(parents=True, exist_ok=False)
    with Database(profiles["source"]) as db:
        before = db.query(f"SELECT COUNT(*),SUM(amount),SUM(customer_id),BIT_XOR(CRC32(note)) FROM `{SCHEMA}`.orders")
        customers_before = db.query(f"SELECT COUNT(*),SUM(region) FROM `{SCHEMA}`.customers")
        items_before = db.query(f"SELECT COUNT(*),SUM(price) FROM `{SCHEMA}`.items")
    yield profiles, root
    with Database(profiles["source"]) as db:
        after = db.query(f"SELECT COUNT(*),SUM(amount),SUM(customer_id),BIT_XOR(CRC32(note)) FROM `{SCHEMA}`.orders")
        assert before == after
        assert customers_before == db.query(f"SELECT COUNT(*),SUM(region) FROM `{SCHEMA}`.customers")
        assert items_before == db.query(f"SELECT COUNT(*),SUM(price) FROM `{SCHEMA}`.items")
    write_json(root / "data-unchanged.json", {"before":before,"after":after})


def check(live, name, sql, parameters=None):
    profiles, root = live
    query = make_query(sql, SCHEMA, allowed=ALLOWED, parameters=parameters)
    observations = []
    for fmt in ("row", "tidb_json"):
        snapshot = capture_plan(query, "old", profiles["old"], format=fmt,
                                stats_retries=5 if name.startswith("layout_") else 1)
        plan = parse_plan(snapshot)
        observations.append((snapshot, plan))
    write_json(root / (name + ".json"), {"query":query,"observations":observations})
    for snapshot, plan in observations:
        assert snapshot.capture_status == "ok" and not snapshot.issues, snapshot.issues
        assert plan.parse_status == "ok", plan.issues


@pytest.mark.parametrize("name,sql", SQLS.items())
def test_extra_sql(live, name, sql):
    check(live, name, sql)


@pytest.mark.parametrize("name,case", PARAMETERS.items())
def test_parameter_boundaries(live, name, case):
    sql, params = case
    check(live, "parameter_" + name, sql, [{"type":k,"value":v} for k,v in params])


@pytest.mark.parametrize("name,definition", LAYOUTS.items())
def test_table_layout(live, name, definition):
    profiles, root = live
    table = "scenario_" + name
    with Database(profiles["source"]) as db:
        db.query(f"USE `{SCHEMA}`")
        existing = db.scalar("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (SCHEMA,table))
        if existing == "0":
            db.query(f"CREATE TABLE `{table}` " + definition)
            for i in range(10):
                db.query(f"INSERT INTO `{table}` VALUES(%s,%s,%s)", (i,i%3,"prefix-"+str(i)))
            db.query(f"ANALYZE TABLE `{table}`")
        assert db.scalar(f"SELECT COUNT(*) FROM `{table}`") == "10"
        write_json(root / (name + "-ddl.json"), {"ddl":db.query(f"SHOW CREATE TABLE `{table}`")[1]})
    check(live, "layout_" + name, f"SELECT * FROM `{table}` WHERE id BETWEEN 2 AND 8 AND v=1")


@pytest.mark.parametrize("name,sql", REJECTED.items())
def test_nonplan_commands_are_explicitly_rejected(live, name, sql):
    profiles, root = live
    query = make_query(sql,SCHEMA,allowed=ALLOWED)
    def no_connection(*args, **kwargs):
        pytest.fail("Unsupported command must not be sent to a database")
    snapshot = capture_plan(query,"old",profiles["old"],db_factory=no_connection)
    write_json(root / ("rejected_"+name+".json"), {"query":query,"snapshot":snapshot})
    assert snapshot.capture_status == "skipped" and query.executable_status == "unsupported"
    assert any(i["code"] in {"SQL_REJECTED","SQL_UNSUPPORTED"} for i in snapshot.issues)
