"""Cancellation and lease ownership for a running production stage."""
from threading import Event
import time

from sqlalchemy.exc import OperationalError, TimeoutError as DatabaseTimeout


class StageControl:
    def __init__(self, store, task, owner, shutdown):
        """从已领取的租约起算本地截止时间，使用单调时钟防止系统时钟跳变。"""
        self.store, self.task, self.owner = store, task, owner
        self.shutdown = shutdown
        self.done = Event()
        self.canceled = False
        self.next_check = 0.0
        expires = task.get('lease_expires_at')
        self.deadline = (time.monotonic() + max(0, expires - time.time())
                         if expires is not None else float('inf'))

    def detached(self):
        """宿主关闭、租约失效或过期时仅退出本地执行。"""
        return self.done.is_set() or self.shutdown.is_set() or time.monotonic() >= self.deadline

    def is_set(self):
        """短暂连接故障时继续跟踪原任务；只有数据库确认的取消才传给远端。"""
        if self.detached() or self.canceled:
            return True
        if time.monotonic() >= self.next_check:
            try:
                self.canceled = self.store.run_state(self.task['run_id']) == 'CANCELED'
            except (OperationalError, DatabaseTimeout):
                pass
            self.next_check = time.monotonic() + 3
        return self.detached() or self.canceled

    def renew(self):
        """续租失败可在上次确认的租期内重试；过期后由调度器恢复同一工作项。"""
        delay = 15
        while not self.done.wait(delay):
            started = time.monotonic()
            if self.detached():
                return
            try:
                owned = self.store.renew_stage(self.task['id'], self.owner, lease_seconds=60)
            except (OperationalError, DatabaseTimeout):
                delay = 2
                continue
            except Exception:
                self.done.set()
                raise
            if not owned:
                self.done.set()
                return
            self.deadline = started + 60
            delay = 15
