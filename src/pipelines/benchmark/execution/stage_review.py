"""Expose the benchmark stages and review tool arguments."""
import time
from pathlib import Path

from pipelines.reviews.service import review
from capabilities.benchmark_design.stages import STAGES


def call(args, directory, config=None):
    workspace = str(Path(directory).parents[1])
    session = args.get('_dsh_context',{}).get('session_id')
    action = args.get('action','status')
    options = {k:args[k] for k in ('stage','key','artifact','review_id','instruction') if k in args}
    if action == 'open' and options.get('stage') not in STAGES:
        raise ValueError('A completed stage, stable key and viewable artifact are required')
    if action == 'open':
        from pipelines.benchmark.execution.review_artifacts import require_stage_artifact
        options['key'] = require_stage_artifact(workspace, session, options['stage'], options.get('artifact', ''))
        current = review(workspace, session)
        if (current['state'] in ('waiting_user', 'paused', 'needs_input', 'check_failed')
                and (current.get('stage'), current.get('key')) != (options['stage'], options.get('key'))):
            raise ValueError('Resolve the current review before opening another stage')
    if action == 'continue':
        current = review(workspace, session, review_id=args.get('review_id'))
        if not current.get('can_continue'):
            raise ValueError('Only the user can end a review early. Wait for its deadline or use the workbench user controls.')
        return current
    if action == 'instruction':
        raise ValueError('Review instructions come from the user through the workbench controls. Apply chat requests by saving a validated plan revision; do not invent a user instruction.')
    if action != 'wait':
        return review(workspace,session,action,**options)
    timeout = args.get('timeout_seconds',60)
    if type(timeout) not in (int,float) or not 0 <= timeout <= 300:
        raise ValueError('Wait timeout must be between 0 and 300 seconds')
    end = time.monotonic()+timeout
    while True:
        result = review(workspace,session,'status',review_id=args.get('review_id'))
        if result['state'] != 'waiting_user' or time.monotonic() >= end:
            return result
        time.sleep(min(1,max(0,end-time.monotonic())))
