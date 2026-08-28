"""Rules-based intervention selection using estimated expected recovery value."""

from __future__ import annotations

from dataclasses import dataclass

from .cohorts import COHORTS, FailureCohort, RecoveryAction
from .erv import expected_recovery_value
from .policy_guard import RecoveryCase


@dataclass(frozen=True)
class StrategyDecision:
    action: RecoveryAction
    recovery_probability: float
    expected_recovery_value: float
    rationale: str


ACTION_MATCH: dict[RecoveryAction, set[FailureCohort]] = {
    RecoveryAction.SMART_RETRY: {FailureCohort.TEMPORARY_BANK_FAILURE, FailureCohort.PROCESSOR_TIMEOUT},
    RecoveryAction.WAIT_AND_RETRY: {FailureCohort.INSUFFICIENT_FUNDS},
    RecoveryAction.PAYMENT_METHOD_UPDATE: {FailureCohort.EXPIRED_CARD, FailureCohort.INVALID_CARD},
    RecoveryAction.CUSTOMER_REMINDER: {FailureCohort.THREE_DS_AUTH_FAILURE, FailureCohort.CHECKOUT_ABANDONED},
    RecoveryAction.ESCALATE: set(),
    RecoveryAction.STOP: set(),
}


def estimate_recovery_probability(case: RecoveryCase, cohort: FailureCohort, action: RecoveryAction) -> float:
    """Observable-only probability estimate used before execution.

    It deliberately does not use simulator-only hidden outcome fields.
    """
    if action is RecoveryAction.STOP:
        return 0.0
    base = COHORTS[cohort].base_recovery_probability
    match_multiplier = 1.20 if cohort in ACTION_MATCH[action] else 0.30
    retry_penalty = 0.82 ** case.retry_attempt_number
    fatigue_penalty = 0.88 ** case.prior_notifications_sent if action in {RecoveryAction.CUSTOMER_REMINDER, RecoveryAction.PAYMENT_METHOD_UPDATE} else 1.0
    fraud_penalty = max(0.05, 1.0 - case.fraud_risk_score)
    probability = base * match_multiplier * retry_penalty * fatigue_penalty * fraud_penalty
    return round(min(0.98, max(0.0, probability)), 6)


class StrategyEngine:
    def choose(self, case: RecoveryCase, cohort: FailureCohort) -> StrategyDecision:
        candidates: list[StrategyDecision] = []
        for action in RecoveryAction:
            probability = estimate_recovery_probability(case, cohort, action)
            erv = expected_recovery_value(case.amount, probability, action, case.prior_notifications_sent)
            candidates.append(StrategyDecision(action, probability, erv, ""))
        best = max(candidates, key=lambda decision: decision.expected_recovery_value)
        if best.action is RecoveryAction.STOP:
            return StrategyDecision(RecoveryAction.STOP, 0.0, 0.0, "No permitted intervention has positive expected recovery value.")
        wait = COHORTS[cohort].retry_wait_minutes
        timing = f" Recommended wait: {wait} minutes." if wait is not None else ""
        return StrategyDecision(
            best.action,
            best.recovery_probability,
            best.expected_recovery_value,
            f"Selected {best.action.value} because it has the highest estimated ERV for {cohort.value}.{timing}",
        )
