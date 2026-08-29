"""Rules-based intervention selection using estimated expected recovery value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cohorts import COHORTS, FailureCohort, RecoveryAction
from .erv import expected_recovery_value

if TYPE_CHECKING:
    from .simulator import SimulatedTransaction


@dataclass(frozen=True, kw_only=True)
class StrategyContext:
    """Observable inputs available before a probability is estimated."""
    transaction_id: str
    amount: float
    fraud_risk_score: float
    opted_out: bool
    retry_attempt_number: int
    prior_notifications_sent: int
    customer_history_score: float


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
    RecoveryAction.ESCALATE: {FailureCohort.RISK_FRAUD_DECLINE},
    RecoveryAction.STOP: set(),
}


def context_from_transaction(transaction: "SimulatedTransaction") -> StrategyContext:
    """Build the observable-only context consumed by the Strategy Engine."""
    return StrategyContext(
        transaction_id=transaction.transaction_id,
        amount=transaction.amount,
        fraud_risk_score=transaction.fraud_risk_score,
        opted_out=transaction.opted_out,
        retry_attempt_number=transaction.retry_attempt_number,
        prior_notifications_sent=transaction.prior_notifications_sent,
        customer_history_score=transaction.customer_history_score,
    )


def estimate_recovery_probability(context: StrategyContext, cohort: FailureCohort, action: RecoveryAction) -> float:
    """Observable-only probability estimate used before execution.

    It deliberately does not use simulator-only hidden outcome fields.
    """
    if action is RecoveryAction.STOP:
        return 0.0
    base = COHORTS[cohort].base_recovery_probability
    match_multiplier = 1.20 if cohort in ACTION_MATCH[action] else 0.30
    retry_penalty = 0.82 ** context.retry_attempt_number
    history_multiplier = 0.70 + 0.60 * context.customer_history_score
    fatigue_penalty = 0.88 ** context.prior_notifications_sent if action in {RecoveryAction.CUSTOMER_REMINDER, RecoveryAction.PAYMENT_METHOD_UPDATE} else 1.0
    fraud_penalty = max(0.05, 1.0 - context.fraud_risk_score)
    probability = base * match_multiplier * retry_penalty * history_multiplier * fatigue_penalty * fraud_penalty
    return round(min(0.98, max(0.0, probability)), 6)


class StrategyEngine:
    def choose(self, context: StrategyContext, cohort: FailureCohort) -> StrategyDecision:
        candidates: list[StrategyDecision] = []
        for action in RecoveryAction:
            probability = estimate_recovery_probability(context, cohort, action)
            erv = expected_recovery_value(context.amount, probability, action, context.prior_notifications_sent)
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
