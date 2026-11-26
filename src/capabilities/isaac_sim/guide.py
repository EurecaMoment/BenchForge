"""Static usage guidance for the isaacsim_rpc.v1 simulation service."""
from capabilities.isaac_sim.grpc.transport import PROTOCOL

OPERATIONS = {
    'health': {'request': {}, 'returns': 'status, api_version, queue_depth, artifacts_free_bytes'},
    'submit': {'request': {'simulation': 'SimulateRequest JSON', 'request_id': 'optional; derived in DSH'},
               'returns': 'job id/state plus a durable receipt; next action is wait'},
    'status': {'request': {'job_id': 'job id'}, 'returns': 'Job with state, phase and artifact manifest'},
    'wait': {'request': {'job_id': 'job id', 'timeout_seconds': '0..60'},
             'returns': 'latest Job; it never resubmits'},
    'cancel': {'request': {'job_id': 'job id'}, 'returns': 'Job after best-effort cancellation'},
    'download': {'request': {'job_id': 'succeeded job id'},
                 'returns': 'verified files, artifact identities and a local receipt'},
}


# 返回静态契约说明，不再读取在线能力目录。
def usage(operation=None):
    if operation is not None and operation not in OPERATIONS:
        raise ValueError(f'Unknown Isaac Sim operation {operation!r}')
    return {'protocol': PROTOCOL, 'service': 'isaacsim_rpc.v1.SimulationService',
            'operations': ({operation: OPERATIONS[operation]} if operation else OPERATIONS),
            'notes': [
                'SubmitJob is idempotent by request_id; keep the receipt to resume polling.',
                'Simulation and rendering run remotely; every artifact is downloaded and sha256-verified locally.',
                'AckJob runs only after the immutable local capture is published.',
                'native/generated assets and upload are not part of this contract; mesh needs a pre-staged mesh.npz path.',
            ]}
