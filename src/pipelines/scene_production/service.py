"""Compose scene business decisions with the transactional store."""

from storage.scene_jobs.store import Store
from pipelines.scene_production.lifecycle import SceneLifecycle


def create_store(root, harness=None, database_url=None):
    return Store(root, harness, database_url, lifecycle=SceneLifecycle(root))
