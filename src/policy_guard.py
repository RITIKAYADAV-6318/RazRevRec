"""Deterministic, explainable safety policy for proposed recovery actions."""

from __future__ import annotations

from dataclasses import dataclass

from .cohorts import CONTACT_ACTIONS, RETRY_ACTIONS, RecoveryAction


@dataclass(frozen=True, kw_only=True)
class RecoveryCase:
    transaction_id: str
    amount: float
    fraud_risk_score: float
    opted_out: bool
    already_recovered: bool
    recovery_probability: float
    retry_attempt_number: int
    prior_notifications_sent: int


@dataclass(frozen=True)
class GuardDecision:
    approved: bool
    action: RecoveryAction
    explanation: str
    # Short, stable machine-readable code identifying WHY this decision was
    # made -- None when approved. Kept separate from `explanation` (free text
    # for humans/audit display) so callers can categorize decisions reliably
    # without string-matching on prose, which would silently break if wording
    # ever changes.
    reason: str | None = None


class PolicyGuard:
    def __init__(self, fraud_threshold: float = 0.70, min_probability: float = 0.20, max_retries: int = 3, max_notifications: int = 2) -> None:
        self.fraud_threshold = fraud_threshold
        self.min_probability = min_probability
        self.max_retries = max_retries
        self.max_notifications = max_notifications

    def evaluate(self, case: RecoveryCase, action: RecoveryAction) -> GuardDecision:
        if case.already_recovered:
            return GuardDecision(False, RecoveryAction.STOP, "Denied: payment has already been recovered (idempotency protection).", reason="already_recovered")
        # ESCALATE is the human-in-the-loop fallback specifically for cases too
        # risky or uncertain for automation -- it is the designated action for
        # the fraud-decline cohort (see simulator.py ACTION_COHORT_EFFECT) and
        # is only ever proposed for low-probability cases. Both the fraud
        # threshold and the probability floor exist to block AUTOMATED actions
        # under exactly those conditions; applying either to ESCALATE would
        # deny it for the very reason it was proposed, making the escalation
        # path permanently unreachable. Idempotency (above) still applies
        # unconditionally -- there's never a reason to escalate an
        # already-recovered payment.
        if action is not RecoveryAction.ESCALATE:
            if case.fraud_risk_score > self.fraud_threshold:
                return GuardDecision(False, RecoveryAction.STOP, "Denied: fraud risk score exceeds the permitted threshold.", reason="fraud_risk")
            if case.recovery_probability < self.min_probability:
                return GuardDecision(False, RecoveryAction.STOP, "Denied: recovery probability is below the minimum threshold.", reason="low_probability")
        if case.opted_out and action in CONTACT_ACTIONS:
            return GuardDecision(False, RecoveryAction.STOP, "Denied: customer has opted out of recovery communications.", reason="opted_out")
        if action in RETRY_ACTIONS and case.retry_attempt_number >= self.max_retries:
            return GuardDecision(False, RecoveryAction.STOP, "Denied: maximum retry attempts reached.", reason="max_retries")
        if action in CONTACT_ACTIONS and case.prior_notifications_sent >= self.max_notifications:
            return GuardDecision(False, RecoveryAction.STOP, "Denied: maximum customer notification limit reached.", reason="max_notifications")
        return GuardDecision(True, action, "Approved: proposed action satisfies all deterministic policy rules.")