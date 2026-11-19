"""Deterministic delivery status for a requested item quantity."""

QUANTITY_CONTRACTS = {"exact", "minimum", "best_effort"}


# 验证数量合同是精确、至少或尽力交付三种受支持模式之一。
def validate_quantity_contract(value):
    if value not in QUANTITY_CONTRACTS:
        raise ValueError(
            "quantity_contract must be exact, minimum or best_effort"
        )
    return value


# 分别报告实际交付结果与数量合同是否达成。
def delivery_status(accepted, target, contract="best_effort"):
    """Report execution output separately from whether its quantity was met."""
    validate_quantity_contract(contract)
    if type(accepted) is not int or accepted < 0:
        raise ValueError("accepted item count must be a non-negative integer")
    if type(target) is not int or target < 1:
        raise ValueError("target_items must be a positive integer")

    target_reached = accepted >= target
    if contract == "exact":
        satisfied = accepted == target
    elif contract == "minimum":
        satisfied = target_reached
    else:
        satisfied = accepted > 0

    status = (
        "complete"
        if satisfied and target_reached
        else "partial"
        if accepted
        else "unsatisfied"
    )
    return {
        "quantity_contract": contract,
        "target_items": target,
        "accepted": accepted,
        "shortfall": max(0, target - accepted),
        "target_reached": target_reached,
        "delivery_satisfied": satisfied,
        "delivery_status": status,
    }
