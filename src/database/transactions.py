"""Short transactions, entity-scoped locks and atomic conflict handling."""

from contextlib import contextmanager
import hashlib
import json

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as postgres_insert


# 提交正常完成的数据库操作；任意异常均回滚并继续向外传播。
@contextmanager
def transaction(engine):
    with engine.connect() as connection:
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


# 将领域与实体身份哈希成事务级咨询锁，串行处理同一业务实体。
def lock_entity(connection, domain, *identity):
    data = json.dumps([domain, *identity], separators=(",", ":")).encode()
    key = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big", signed=True)
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


# 为目标表创建 PostgreSQL 插入语句，供冲突处理复用。
def insert_statement(connection, table):
    return postgres_insert(table)


# 依据指定唯一键忽略重复插入，支持幂等初始化记录。
def insert_missing(connection, table, values, keys):
    return connection.execute(insert_statement(connection, table).values(**values)
                              .on_conflict_do_nothing(index_elements=keys))
