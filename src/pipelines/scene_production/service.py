"""Compose scene business decisions with the transactional store."""

from storage.scene_jobs.store import Store
from pipelines.scene_production.lifecycle import SceneLifecycle


# 装配共享任务存储与场景生命周期规则，供服务和迁移入口复用。
def create_store(root, harness=None, database_url=None):
    return Store(root, harness, database_url, lifecycle=SceneLifecycle(root))
