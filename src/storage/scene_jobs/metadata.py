"""Compatibility facade for the shared BenchForge database package."""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import select

from database.engine import engine_for
from database.migrations import initialize_schema
from database.schema import events, runs, work_items as tasks
from database.transactions import lock_entity, transaction


class Repository:
    """Transactional scene repository backed by the configured PostgreSQL pool."""

    # 复用给定数据库连接池并初始化场景任务所需结构。
    def __init__(self, url: str):
        self.engine = engine_for(url)
        initialize_schema(self.engine)

    # 为启动时的协议迁移取得全局事务锁，普通任务不使用此锁。
    def lock_queue(self, conn):
        """Lock the explicit startup protocol migration, never ordinary jobs."""
        lock_entity(conn, "scene-protocol-migration")

    # 提供自动提交或异常回滚的场景任务数据库事务。
    @contextmanager
    def transaction(self):
        with transaction(self.engine) as conn:
            yield conn

    # 在当前事务内追加带时间和载荷的运行或工作项事件。
    @staticmethod
    def event(conn, run_id, task_id, event, payload=None):
        conn.execute(events.insert().values(
            run_id=run_id,
            work_item_id=task_id,
            event=event,
            at=datetime.now(timezone.utc).isoformat(),
            payload=payload or {},
        ))

    # 读取运行及按编号排序的工作项快照，未知运行明确报错。
    def status(self, run_id):
        with self.engine.connect() as conn:
            run = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
            if run is None:
                raise FileNotFoundError(f"Unknown run: {run_id}")
            work = conn.execute(
                select(tasks).where(tasks.c.run_id == run_id).order_by(tasks.c.id)
            ).mappings()
            return {**dict(run), "tasks": [dict(row) for row in work]}
