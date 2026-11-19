"""Compose scene authoring diagnostics with the selected capture adapter."""
from functools import partial
from pathlib import Path

from capabilities.isaac_sim.scene_requirements import capture_requirement_issues
from capabilities.scene_authoring.program_submission import (
    inspect_program_submission,
    read_program_submission,
)
from capabilities.scene_authoring.validation.preflight import inspect_program


def inspect_capture_program(root, program, *, settings=None, **options):
    issues = capture_requirement_issues(program, Path(root) / 'generated_assets', settings)
    report = inspect_program(root, program, **options, additional_issues=issues)
    if report['passed'] and settings and settings.get('grpc'):
        from capabilities.isaac_sim.capture_contract import inspect_live_capture_service
        from capabilities.isaac_sim.scene_requirements import UnsupportedScene
        try:
            live = inspect_live_capture_service(program, settings)
        except UnsupportedScene as error:
            live = error.issues
        except (ValueError, OSError) as error:
            live = [{'code': 'service_contract_check_failed', 'path': '', 'message': str(error),
                     'basis': 'live_isaac_sim_schema', 'severity': 'error', 'blocking': True,
                     'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': False}]
        if live:
            from capabilities.scene_authoring.validation.diagnostics import SceneDiagnostics
            diagnostics = SceneDiagnostics()
            diagnostics.issues = [*report['issues'], *live]
            diagnostics.skipped = report['skipped_checks']
            diagnostics.checked = report['checked']
            report.update(diagnostics.finish(program, enforced=report['enforced']))
        report['service_contract'] = {'status': 'incompatible' if live else 'compatible',
                                      'source': 'live GetCapabilities', 'simulation_executed': False}
    return report


def inspect_capture_submission(root, source_path, *, execution_mode=None, settings=None):
    return inspect_program_submission(root, source_path, execution_mode=execution_mode,
                                      inspector=partial(inspect_capture_program, settings=settings))


def read_capture_submission(root, source_path, *, execution_mode=None, settings=None):
    return read_program_submission(root, source_path, execution_mode=execution_mode,
                                   inspector=partial(inspect_capture_program, settings=settings))
