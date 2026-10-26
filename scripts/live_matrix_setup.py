"""Extend only the explicitly approved fixture schema. No business objects touched."""
import argparse
import json
from pathlib import Path

from pcc_tool.capture.connection import Database, load_profiles

SCHEMA = "pcc_validation_20261011"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    profile = load_profiles(args.config)["source"]
    assert profile.get("expected_version")
    with Database(profile) as db:
        version = db.version()
        assert db.scalar("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (SCHEMA,)) == "1"
        assert db.scalar("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='matrix_types'", (SCHEMA,)) == "0", "Fixture already exists; do not overwrite it"
        db.query(Path("tests/fixtures/sql_matrix_setup.sql").read_text())
        columns = "id,tiny,small,medium,signed_int,signed_big,unsigned_big,f,d,money,precise_value,bits,flag,fixed_char,text_value,ci_value,binary_value,varbinary_value,tiny_text,text_body,medium_text,long_text,tiny_blob,blob_value,medium_blob,long_blob,day_value,dt,ts,duration,yr,enum_value,set_value,doc,nullable_value"
        for i in range(32):
            values = [i,i-16,i*100,i*1000,i,i*1000000,2**63+i,1.25,1.125,
                      f"{i}.123456", "123456789012345678901234567890.123456789012345678901234567890",
                      i,i%2,"fixed",f"中文😀'?,;\\%s-{i}",f"Case-{i}",b"a\x00b",b"\xff\x00",
                      "tiny","body","medium","long",b"tiny",b"\xff\x00",b"medium",b"long",
                      "2024-02-29","2024-02-29 12:34:56.123456","2024-02-29 12:34:56.123456",
                      "-12:34:56.123456",2024,"paid","a,c",json.dumps({"n":i,"s":"?,;中文"}),None if i%2 else i]
            db.query(f"INSERT INTO `{SCHEMA}`.matrix_types ({columns}) VALUES ({','.join(['%s']*len(values))})", tuple(values))
        db.query(f"ANALYZE TABLE `{SCHEMA}`.matrix_types")
        print(json.dumps({"version":version,"schema":SCHEMA,"table":"matrix_types","rows":db.scalar(f"SELECT COUNT(*) FROM `{SCHEMA}`.matrix_types")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
