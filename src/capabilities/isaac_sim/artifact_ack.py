"""Acknowledge retained job artifacts after their S2 capture has been published."""

import json
import logging

import grpc

from capabilities.isaac_sim.grpc.unified_transport import TRANSIENT
from storage.files import write_json


def prepare_acknowledgments(attempt, token, endpoint, job_ids):
    if job_ids:
        write_json(attempt / 'artifact_ack.json', {'capture_token': token, 'endpoint': endpoint,
                   'pending': list(dict.fromkeys(job_ids)), 'acknowledged': {}, 'errors': {}})


def acknowledge_published(attempt, destination, token, client=None):
    """A failed acknowledgment leaves the published capture usable and the pending IDs durable."""
    path = attempt / 'artifact_ack.json'
    if not path.is_file():
        return
    own_client = client is None
    try:
        ledger = json.loads(path.read_text(encoding='utf-8'))
        if not ledger['pending']:
            return
        report = json.loads((destination / 'report.json').read_text(encoding='utf-8'))
        if ledger['capture_token'] != token or report.get('token') != token:
            raise ValueError('Capture publication does not match the artifact acknowledgment receipt')
        if client is None:
            from capabilities.isaac_sim.capture_contract import capture_service_client
            settings = json.loads((attempt / 'capture.json').read_text(encoding='utf-8'))['settings']
            client = capture_service_client({'providers': {'IsaacSim4A100': settings}})
        if client.endpoint != ledger['endpoint']:
            raise ValueError('Retained job artifacts belong to a different simulator endpoint')
        for job_id in ledger['pending'][:]:
            for retry in (True, False):
                try:
                    response = client.ack(job_id, timeout=3)
                except grpc.RpcError as error:
                    if retry and error.code() in TRANSIENT:
                        continue
                    ledger['errors'][job_id] = {'code': error.code().name, 'detail': error.details()}
                    logging.getLogger(__name__).warning('Isaac Sim artifact acknowledgment pending for %s: %s',
                                                        job_id, error.code().name)
                else:
                    ledger['pending'].remove(job_id)
                    ledger['errors'].pop(job_id, None)
                    ledger['acknowledged'][job_id] = {'released': response.released,
                                                       'freed_bytes': response.freed_bytes}
                break
            write_json(path, ledger)
    except (OSError, ValueError, TypeError, KeyError) as error:
        logging.getLogger(__name__).warning('Could not acknowledge published Isaac Sim artifacts: %s', error)
    finally:
        if own_client and client is not None:
            client.close()
