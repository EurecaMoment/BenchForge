"""The callable guide reads live discovery without submitting work or scanning a checkout."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import grpc

from capabilities.isaac_sim.grpc import simulator_pb2 as pb
from capabilities.isaac_sim.grpc.simulator_pb2_grpc import SimulatorServicer, add_SimulatorServicer_to_server
from capabilities.isaac_sim.tool_api import execute


class DiscoveryService(SimulatorServicer):
    # 初始化可切换的能力可用性与服务调用记录。
    def __init__(self):
        self.availability = pb.AVAILABLE
        self.calls = []

    # 返回测试能力及当前可用性，用于区分已声明与已验证支持。
    def GetCapabilities(self, request, context):
        self.calls.append('GetCapabilities')
        return pb.Capabilities(api_version='a100sim.v1', capabilities=[
            pb.Capability(name='extension.asset.import', availability=self.availability,
                          scope='USD only', details={'parameter_schema': {'type': 'object'}}),
            pb.Capability(name='extension.extended.asset.import', availability=pb.AVAILABLE,
                          scope='Separate extended sessions', details={'parameter_schema': {
                              'type': 'object', 'properties': {'path': {'type': 'string'}},
                              'required': ['path']}})])

    # 返回含字段和功能身份的在线 API 目录夹具。
    def GetApiCatalog(self, request, context):
        self.calls.append('GetApiCatalog')
        return pb.ApiCatalog(api_version='a100sim.v1', domains=[pb.ApiDomain(
            id='assets', title='Assets', features=[pb.Feature(id='asset-load', title='Import asset',
            support=pb.UNVERIFIED, availability=self.availability,
            request_schema={'type': 'object', 'required': ['source']}),
            pb.Feature(id='sessions.extended.asset.import', title='Import extended asset',
            support=pb.PARTIAL, availability=pb.AVAILABLE,
            request_schema={'type': 'object', 'properties': {'path': {'type': 'string'}},
                            'required': ['path']})])])


class GuideTests(unittest.TestCase):
    # 启动独立本地发现服务与临时目录，供在线使用指南测试。
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = DiscoveryService()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown)
        self.server = grpc.server(self.pool)
        add_SimulatorServicer_to_server(self.service, self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.addCleanup(lambda: self.server.stop(0).wait())
        self.config = {'providers': {'IsaacSim4A100': {'grpc': {'host': '127.0.0.1', 'port': port}}}}

    # 调用只读发现入口，并断言该操作不会初始化任务所有权数据库或请求日志。
    def guide(self, **args):
        # Discovery must not initialize the ownership database or request journal.
        with patch('capabilities.isaac_sim.tool_api.NativeJobs', side_effect=AssertionError('Not a job action')):
            return execute({'action': 'guide', **args}, self.root / 'operations' / 'guide', self.config)

    # 验证按操作筛选仍保留不完整在线 schema，不凭空补造参数。
    def test_live_operation_filter_preserves_incomplete_schema_without_inventing_parameters(self):
        result = self.guide(operation='extension.asset.import')
        self.assertEqual(self.service.calls, ['GetCapabilities', 'GetApiCatalog'])
        live = result['result']['live']['operations'][0]
        self.assertEqual(live['details']['parameter_schema'], {'type': 'object'})
        self.assertEqual(live['availability'], 'AVAILABLE')
        self.assertEqual(json.loads(Path(result['receipt_path']).read_text()), result['result'])
        self.assertFalse((self.root / '.benchforge').exists())
        self.service.availability = pb.UNAVAILABLE
        self.assertEqual(self.guide(operation='extension.asset.import')['result']['live']['operations'][0]
                         ['availability'], 'UNAVAILABLE')

    # 验证功能筛选采用目录真实身份，并保留尚未验证的支持状态。
    def test_feature_filter_preserves_unverified_support_and_uses_catalog_feature_identity(self):
        result = self.guide(feature_id='asset-load')['result']
        self.assertEqual(result['live']['features'][0]['support'], 'UNVERIFIED')
        self.assertEqual(result['live']['features'][0]['request_schema']['required'], ['source'])
        with self.assertRaisesRegex(ValueError, 'absent from the live Isaac Sim catalog'):
            self.guide(feature_id='extension.asset.import')
        with self.assertRaisesRegex(ValueError, 'absent from the live Isaac Sim capabilities'):
            self.guide(operation='retired.operation')

    # 验证默认指南精简在线详情，同时完整说明工具的任务生命周期。
    def test_default_guide_is_compact_and_contains_the_complete_tool_lifecycle(self):
        result = self.guide()['result']
        self.assertEqual(result['live']['features'], [])
        self.assertNotIn('details', result['live']['operations'][0])
        self.assertEqual(result['live']['feature_domains'][0]['feature_count'], 2)
        self.assertEqual(set(result['actions']), {'guide', 'capabilities', 'catalog', 'assets', 'upload',
                                                'submit', 'invoke', 'status', 'wait', 'cancel', 'download'})

    # 验证标准与扩展操作族各自保留在线 schema 和支持状态。
    def test_standard_and_extended_families_preserve_separate_live_schemas_and_support(self):
        standard = self.guide(operation='extension.asset.import')['result']['live']['operations']
        extended = self.guide(operation='extension.extended.asset.import')['result']['live']['operations']
        self.assertEqual(len(standard), 1)
        self.assertEqual(len(extended), 1)
        self.assertEqual(standard[0]['details']['parameter_schema'], {'type': 'object'})
        self.assertEqual(extended[0]['details']['parameter_schema']['required'], ['path'])
        self.assertEqual(extended[0]['scope'], 'Separate extended sessions')
        feature = self.guide(feature_id='sessions.extended.asset.import')['result']['live']['features'][0]
        self.assertEqual(feature['support'], 'PARTIAL')
        self.assertEqual(feature['request_schema']['properties'], {'path': {'type': 'string'}})
