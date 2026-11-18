"""Retire the old capture queue protocol without replaying or losing old work."""
import time
import uuid

from sqlalchemy import MetaData, Table, inspect, select

from database.schema import domain_migrations as migrations

from storage.files import write_json
from storage.scene_jobs.metadata import runs, tasks


MIGRATION = 'a100-grpc-v1'


def migrate_capture(store):
    """Run once before scheduling, while the previous service is stopped.

    The SQL receipt commits with the queue changes. A crash before commit leaves
    the original rows intact and an extra backup, never a false completion marker.
    Historical captures and all task directories remain readable.
    """
    with store.repo.transaction() as connection:
        store.repo.lock_queue(connection)
        prior = connection.execute(select(migrations).where(migrations.c.id == MIGRATION)).mappings().first()
        if prior:
            return {'migration': MIGRATION, 'already_applied': True, 'backup_path': prior['backup_path']}
        legacy = None
        devices = []
        if inspect(connection).has_table('benchforge_devices'):
            legacy = Table('benchforge_devices', MetaData(), autoload_with=connection)
            devices = [dict(row) for row in connection.execute(select(legacy)).mappings()]
        affected = []
        for row in connection.execute(select(tasks).where(
                tasks.c.state.in_(['PENDING', 'READY', 'RUNNING']))).mappings():
            unit = row['unit'] or {}
            if unit.get('phase') != 'capture':
                continue
            directory = store.directory(row['id'], unit['revision'])
            if any((directory / 'isaac_sim').glob('attempt_*/capture.json')):
                continue
            affected.append(dict(row))
        run_ids = {row['run_id'] for row in affected}
        previous_runs = [dict(row) for row in connection.execute(
            select(runs).where(runs.c.id.in_(run_ids))).mappings()] if run_ids else []
        backup = None
        if devices or affected:
            backup = store.root / 'migrations' / (MIGRATION + '-' + uuid.uuid4().hex + '.json')
            write_json(backup, {'migration': MIGRATION, 'created_at': time.time(),
                'devices': devices, 'tasks': affected, 'runs': previous_runs})
            backup.chmod(0o600)
        run_states = {row['id']: row['state'] for row in previous_runs}
        for task in affected:
            state = 'CANCELED' if run_states[task['run_id']] == 'CANCELED' else 'FAILED_FINAL'
            failure = {'stage': 'capture_execution', 'code': 'CAPTURE_PROTOCOL_MIGRATED',
                'detail': 'The prior capture protocol was retired. Artifacts and history are preserved. '
                          'Retry this task explicitly to submit it to a100sim.v1.Simulator.',
                'backup_path': str(backup)}
            connection.execute(tasks.update().where(tasks.c.id == task['id']).values(
                state=state, lease_owner=None, lease_expires_at=None, failure=failure))
            store.repo.event(connection, task['run_id'], task['id'],
                'benchforge.capture_protocol_migrated', failure)
        if legacy is not None:
            legacy.drop(connection)
        connection.execute(migrations.insert().values(
            id=MIGRATION, backup_path=str(backup) if backup else None, applied_at=str(time.time())))
    store.settle_runs()
    return {'migration': MIGRATION, 'already_applied': False, 'retired_tasks': len(affected),
            'backup_path': str(backup) if backup else None}
