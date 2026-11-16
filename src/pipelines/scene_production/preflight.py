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
    return inspect_program(root, program, **options, additional_issues=capture_requirement_issues(
        program, Path(root) / 'generated_assets', settings))


def inspect_capture_submission(root, source_path, *, execution_mode=None, settings=None):
    return inspect_program_submission(root, source_path, execution_mode=execution_mode,
                                      inspector=partial(inspect_capture_program, settings=settings))


def read_capture_submission(root, source_path, *, execution_mode=None, settings=None):
    return read_program_submission(root, source_path, execution_mode=execution_mode,
                                   inspector=partial(inspect_capture_program, settings=settings))
