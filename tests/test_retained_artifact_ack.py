import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock

import grpc

from capabilities.isaac_sim.artifact_ack import acknowledge_published, prepare_acknowledgments
from capabilities.isaac_sim.capture_stage import execute_capture
from capabilities.isaac_sim.grpc import simulation_pb2 as pb
from capabilities.isaac_sim.grpc.receipts import request_id
from storage.files import write_json


class Busy(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return 'worker restarting'


class RetainedArtifactAckTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.attempt = self.root / 'isaac_sim/attempt_0'
        self.destination = self.root / 'capture'
        self.token = request_id(str(self.root), 'capture-0')
        self.client = Mock(endpoint='sim:18554')
        prepare_acknowledgments(self.attempt, self.token, self.client.endpoint, ['views', 'export'])

    def ledger(self):
        return json.loads((self.attempt / 'artifact_ack.json').read_text())

    def test_unpublished_or_wrong_endpoint_cannot_release(self):
        acknowledge_published(self.attempt, self.destination, self.token, self.client)
        write_json(self.destination / 'report.json', {'token': 'other'})
        acknowledge_published(self.attempt, self.destination, self.token, self.client)
        write_json(self.destination / 'report.json', {'token': self.token})
        self.client.endpoint = 'different:18554'
        acknowledge_published(self.attempt, self.destination, self.token, self.client)
        self.client.ack.assert_not_called()
        self.assertEqual(self.ledger()['pending'], ['views', 'export'])

    def test_retry_pending_after_publication_without_resubmitting_or_redownloading(self):
        write_json(self.destination / 'report.json', {'token': self.token})
        self.client.ack.side_effect = [pb.AckJobResponse(released=False), Busy(), Busy()]
        acknowledge_published(self.attempt, self.destination, self.token, self.client)
        self.assertEqual(self.ledger()['pending'], ['export'])
        self.assertEqual(self.ledger()['acknowledged']['views'], {'released': False, 'freed_bytes': 0})
        self.assertEqual(self.ledger()['errors']['export']['code'], 'UNAVAILABLE')
        store = Mock()
        store.directory.return_value = self.root
        task = {'id': 'task', 'unit': {'revision': 0}}
        self.client.ack.side_effect = [pb.AckJobResponse(released=True, freed_bytes=24)]
        execute_capture(store, task, Event(), config={}, client=self.client)
        execute_capture(store, task, Event(), config={}, client=self.client)
        self.assertEqual([call.args[0] for call in self.client.ack.call_args_list],
                         ['views', 'export', 'export', 'export'])
        self.assertEqual(self.ledger()['acknowledged']['export'], {'released': True, 'freed_bytes': 24})
        self.assertEqual(self.ledger()['pending'], [])
        self.client.submit.assert_not_called()
        self.client.download.assert_not_called()
        store.publish_capture.assert_not_called()
