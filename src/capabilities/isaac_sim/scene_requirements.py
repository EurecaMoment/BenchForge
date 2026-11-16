"""Admission checks for the SceneProgram adapter, separate from native RPC tools."""


class UnsupportedScene(ValueError):
    """An explicit requirement has no faithful SceneProgram adapter mapping."""


def validate_scene_requirements(program):
    executable = [
        row
        for row in program.get("interactions", program.get("affordances", []))
        if row.get("action") in ("apply_force", "robot_push", "record_trajectory")
    ]
    if executable:
        raise UnsupportedScene(
            "This SceneProgram action requires native session commands and world.collect receipts. "
            "Use benchforge_isaac_sim with the live catalog before submitting a capture; "
            "robot/contact/trajectory execution cannot be silently substituted."
        )
