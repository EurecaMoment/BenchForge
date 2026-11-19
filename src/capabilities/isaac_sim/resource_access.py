"""Conversation ownership for native retained worlds and uploaded asset handles."""

import hashlib
from database.engine import engine_for
from service_access.access_scope import current_access
from storage.access_documents import read_document, write_document, DocumentConflict

# 这些目录能力映射到 import_robot/convert_asset，只处理已上传资产，不操作驻留世界。
_ASSET_FEATURES = frozenset({'assets.import_robot', 'assets.convert_glb', 'robot_types.robot_assets'})


class NativeResources:
    # 保存当前会话访问范围，并按远程端点建立独立的原生资源授权命名空间。
    def __init__(self, endpoint):
        self.access = current_access()
        self.engine = engine_for() if self.access else None
        self.namespace = 'isaac-' + hashlib.sha256(endpoint.encode()).hexdigest()

    # 校验资源身份长度后生成按种类与摘要隔离的授权记录文件名。
    def _name(self, kind, value):
        if not isinstance(value, str) or not value or len(value) > 512:
            raise PermissionError('Invalid native resource identity')
        return kind + '/' + hashlib.sha256(value.encode()).hexdigest() + '.json'

    # 检查当前会话是否已获指定原生世界或资产的授权；无会话范围时沿用本地调用。
    def owns(self, kind, value):
        if self.access is None:
            return True
        row = read_document(self.engine, self.access.scope, self.namespace, self._name(kind, value))
        return bool(row and row['document'].get('id') == value)

    # 要求当前会话拥有原生资源，否则拒绝该操作。
    def require(self, kind, value):
        if not self.owns(kind, value):
            raise PermissionError('Native ' + kind + ' is not granted to this conversation')

    # 幂等登记成功操作得到的资源身份及来源，并处理并发写入冲突。
    def remember(self, kind, value, *, origin):
        if self.access is None:
            return
        name = self._name(kind, value)
        if self.owns(kind, value):
            return
        try:
            write_document(self.engine, self.access.scope, self.namespace, name,
                           {'id': value, 'origin': origin}, expected_revision=0)
        except DocumentConflict:
            self.require(kind, value)

    # 检查原生请求中的会话与资产所有权，拒绝服务路径输入，并只放行明确的创建或资产操作例外。
    def check(self, payload, *, feature=False):
        if self.access is None:
            return
        if feature:
            operation, arguments = payload['feature_id'], payload.get('arguments', {})
        else:
            extension = payload.get('extension')
            if not extension:
                if set(payload) <= {'convert_asset', 'import_robot'}:
                    arguments, operation = payload, 'asset operation'
                else:
                    raise PermissionError('This global simulator operation has no isolated world identity; use a retained session from the live catalog')
            else:
                operation, arguments = extension['operation'], extension.get('arguments', {})
        found_session = False

        # 递归检查参数中的世界和资产句柄，阻止以路径或字符串来源绕过上传授权。
        def visit(value, key=''):
            nonlocal found_session
            if isinstance(value, dict):
                for field, child in value.items():
                    visit(child, field)
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif isinstance(value, str):
                if key in ('session_id', 'world_id'):
                    self.require('session', value)
                    found_session = True
                elif key == 'asset_id':
                    self.require('asset', value)
                elif key in ('source_path', 'output_path', 'input_path', 'filename', 'file_path'):
                    raise PermissionError('Native service filesystem paths are not task inputs; upload an authorized asset handle')
                elif key == 'source' and value:
                    raise PermissionError('Native source must use an uploaded asset handle')
        visit(arguments)
        creates = operation.endswith(('asset.import', 'sessions.asset.import', 'world.create', 'scene.create'))
        asset_operation = operation == 'asset operation' or (feature and operation in _ASSET_FEATURES)
        if not found_session and not creates and not asset_operation:
            raise PermissionError('Native operation requires an owned retained session')

    # 从成功创建、导入或克隆任务的结果中收集新世界句柄，登记给当前会话。
    def observe(self, job):
        if self.access is None or job.get('state') != 'SUCCEEDED':
            return
        operation = job.get('operation', '')
        if not operation.endswith(('asset.import', 'world.create', 'scene.create', 'scene.clone')):
            return
        # 递归提取成功结果中的session_id或world_id，并保存对应任务来源。
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in ('session_id', 'world_id') and isinstance(child, str):
                        self.remember('session', child, origin=job['job_id'])
                    elif isinstance(child, (dict, list)):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(job.get('result', {}))
