"""Expected Recovery Value scoring with customer-fatigue cost."""

from __future__ import annotations

from .cohorts import ACTION_COSTS, RecoveryAction


def fatigue_cost(prior_notifications_sent: int, unit_cost: float = 12.0) -> float:
    """Soft customer-experience cost that increases faster than linearly."""
    if prior_notifications_sent < 0:
        raise ValueError("prior_notifications_sent cannot be negative")
    return unit_cost * prior_notifications_sent**2


def expected_recovery_value(
    amount: float,
    recovery_probability: float,
    action: RecoveryAction,
    prior_notifications_sent: int = 0,
) -> float:
    """Return net expected recovered value. STOP deliberately has zero value."""
    if amount < 0:
        raise ValueError("amount cannot be negative")
    if not 0 <= recovery_probability <= 1:
        raise ValueError("recovery_probability must be between 0 and 1")
    if action is RecoveryAction.STOP:
        return 0.0
    contact_penalty = fatigue_cost(prior_notifications_sent) if action.value in {"payment_method_update", "customer_reminder"} else 0.0
    return amount * recovery_probability - ACTION_COSTS[action] - contact_penalty

