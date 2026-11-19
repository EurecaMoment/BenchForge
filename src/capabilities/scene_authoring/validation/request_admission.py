"""Validate scene service requests against policy and executable adapter support."""

import json
from capabilities.scene_authoring.validation.policy import (
    policy_for,
    resolve_execution_mode,
    check_references,
)


# 在创建、采集和修订等入口按各意图执行生产策略准入检查。
def check_request(root, route, arguments):
    intents = arguments.get("intents", []) if route == "/start" else [arguments]
    if route == "/start" and not intents:
        return
    if route not in {"/start", "/capture", "/refine", "/extend", "/retry"}:
        return

    route_policies = []
    for intent in intents:
        requested_mode = intent.get("execution_mode", arguments.get("execution_mode"))
        mode = resolve_execution_mode(requested_mode)
        route_policies.append((intent, mode, policy_for(root, requested_mode)))
    if not any(policy for _, _, policy in route_policies):
        return
    for _, _, policy in route_policies:
        if policy:
            check_references(arguments, policy)
    if route == "/retry":
        from capabilities.scene_authoring.contracts.scene_design import capture_retry_directory
        from capabilities.scene_authoring.validation.acceptance import admit_program

        if arguments.get("feedback") is not None:
            raise ValueError("Scene design changes require capture/refine, not review retries")
        directory = capture_retry_directory(root, arguments.get("task_id"))
        admit_program(
            root,
            json.loads((directory / "program.json").read_text()),
            execution_mode=mode,
        )
        return
    if route in {"/start", "/capture", "/refine", "/extend"}:
        for intent, mode, policy in route_policies:
            path = intent.get("scene_program_path")
            must_submit_program = mode == "production"
            if must_submit_program and not path:
                raise ValueError(
                    "The unchecked default planner is disabled; provide scene_program_path"
                )
            if path and policy:
                check_references(path, policy)
            # The submission reader validates and freezes the file once at the
            # application boundary; the lifecycle still protects direct callers.
