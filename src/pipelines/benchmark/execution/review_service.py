"""Shared backend policy for runner, tools, workbench and host timers."""
from pathlib import Path
import time

from storage import stage_reviews as ledger
from storage.execution_lock import exclusive_run, RunBusyError
from storage.review_progress import check_reason
from pipelines.benchmark.execution.review_checks import check_artifact


def review(workspace, session, action='status', *, now=None, **options):
    clock = time.time() if now is None else now
    result = ledger.review(workspace, session, action, now=clock, **options)
    if not result.get('review_id'):
        return result
    row = ledger.snapshot(workspace, session, result['review_id'])
    retry = action in ('retry', 'continue')
    if not check_reason(row, clock, retry=retry):
        return result
    try:
        # Scope the OS lock to one review. Pauses/instructions do not acquire it;
        # the revision comparison rejects a check completed after user input.
        with exclusive_run(Path(workspace) / 'review-locks' / str(row['id'])):
            row = ledger.snapshot(workspace, session, row['id'])
            reason = check_reason(row, clock, retry=retry)
            if not reason:
                return ledger.review(workspace, session, review_id=row['id'], now=clock)
            try:
                receipt = {'passed': True, **check_artifact(workspace, session, row)}
            except (ValueError, OSError, KeyError, TypeError) as error:
                receipt = {'passed': False, 'artifact': row['artifact'], 'error': str(error)}
            finished = time.time() if now is None else now
            receipt['checked_at'] = finished
            return ledger.complete_check(workspace, row, reason, receipt, now=finished)
    except RunBusyError:
        # Another caller owns the same check. Polling must not count a round.
        return ledger.review(workspace, session, review_id=row['id'], now=clock)


def gate(workspace, session, stage, key, artifact):
    return review(workspace, session, 'open', stage=stage, key=key, artifact=artifact)
