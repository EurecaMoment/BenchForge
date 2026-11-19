"""Batch and cache canonical scene receipts for one production operation."""
from service_access.scene_service import service_call


class SceneReceipts:
    # 绑定服务配置和调用入口，并为本批次初始化生产验收回执缓存。
    def __init__(self, config_path, call=service_call):
        self.config_path = config_path
        self.call = call
        self.receipts = {}

    # 按任务及修订号批量取得真实生产验收回执，验证来源并复用已缓存结果。
    def require(self, sources):
        if not self.config_path:
            raise ValueError('Production acceptance requires the configured BenchForge service')
        pending = {}
        for source in sources:
            task_id, revision = source.get('task_id'), source.get('revision')
            if not task_id or type(revision) is not int:
                raise ValueError('Evidence provenance must include the captured task_id and revision')
            key = (task_id, revision)
            if key not in self.receipts:
                pending[key] = {'task_id': task_id, 'revision': revision}
        if pending:
            result = self.call('/production/acceptance', {'scene_sources': list(pending.values())}, self.config_path)
            for row in result.get('scenes', []):
                if row.get('state') != 'accepted' or row.get('authority') != 'production-service-task-record':
                    raise ValueError('Production service did not verify this scene')
                self.receipts[(row['task_id'], row['revision'])] = row
            if any(key not in self.receipts for key in pending):
                raise ValueError('Production service omitted a required scene acceptance')
        return [self.receipts[(row['task_id'], row['revision'])] for row in sources]

    # 从数据记录的来源字段提取任务身份，取得相应生产验收回执。
    def for_record(self, record):
        return self.require([record.get('provenance') or {}])[0]
