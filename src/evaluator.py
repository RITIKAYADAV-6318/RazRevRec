"""Reproducible batch evaluation for the baseline and RazRevRec strategies."""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditTrail
from .cohorts import RecoveryAction
from .policy_guard import PolicyGuard, RecoveryCase
from .simulator import SimulatedTransaction, simulate_outcome
from .strategy import StrategyEngine, estimate_recovery_probability


@dataclass(frozen=True)
class EvaluationMetrics:
    transactions: int
    at_risk_amount: float
    recovered_amount: float
    intervention_cost: float
    net_recovered_value: float
    customer_contacts: int
    approved_actions: int
    policy_denials: int


def _case_from(transaction: SimulatedTransaction, probability: float) -> RecoveryCase:
    return RecoveryCase(
        transaction_id=transaction.transaction_id,
        amount=transaction.amount,
        fraud_risk_score=transaction.fraud_risk_score,
        opted_out=transaction.opted_out,
        already_recovered=False,
        recovery_probability=probability,
        retry_attempt_number=transaction.retry_attempt_number,
        prior_notifications_sent=transaction.prior_notifications_sent,
    )


def _baseline_action(transaction: SimulatedTransaction) -> RecoveryAction:
    return RecoveryAction.SMART_RETRY if transaction.retry_attempt_number < 2 else RecoveryAction.STOP


def evaluate_batch(
    transactions: list[SimulatedTransaction],
    mode: str = "razrevrec",
    audit_trail: AuditTrail | None = None,
) -> EvaluationMetrics:
    """Evaluate one strategy on an identical synthetic holdout batch."""
    if mode not in {"razrevrec", "baseline"}:
        raise ValueError("mode must be 'razrevrec' or 'baseline'")
    guard = PolicyGuard()
    engine = StrategyEngine()
    at_risk = recovered = cost = 0.0
    contacts = approved = denied = 0
    for transaction in transactions:
        at_risk += transaction.amount
        if mode == "razrevrec":
            preliminary = _case_from(transaction, 0.0)
            proposal = engine.choose(preliminary, transaction.cohort)
            action = proposal.action
            probability = proposal.recovery_probability
        else:
            action = _baseline_action(transaction)
            preliminary = _case_from(transaction, 0.0)
            probability = estimate_recovery_probability(preliminary, transaction.cohort, action)
        case = _case_from(transaction, probability)
        decision = guard.evaluate(case, action)
        if audit_trail:
            audit_trail.append("diagnosis", {"transaction_id": transaction.transaction_id, "cohort": transaction.cohort.value, "probability": probability})
            audit_trail.append("strategy", {"transaction_id": transaction.transaction_id, "mode": mode, "proposed_action": action.value})
            audit_trail.append("policy_guard", {"transaction_id": transaction.transaction_id, "approved": decision.approved, "explanation": decision.explanation})
        if not decision.approved:
            denied += 1
            continue
        approved += 1
        outcome = simulate_outcome(transaction, action)
        cost += outcome.intervention_cost
        if action in {RecoveryAction.CUSTOMER_REMINDER, RecoveryAction.PAYMENT_METHOD_UPDATE}:
            contacts += 1
        if outcome.recovered:
            recovered += transaction.amount
        if audit_trail:
            audit_trail.append("outcome", {"transaction_id": transaction.transaction_id, "action": action.value, "recovered": outcome.recovered, "cost": outcome.intervention_cost})
    return EvaluationMetrics(
        transactions=len(transactions),
        at_risk_amount=round(at_risk, 2),
        recovered_amount=round(recovered, 2),
        intervention_cost=round(cost, 2),
        net_recovered_value=round(recovered - cost, 2),
        customer_contacts=contacts,
        approved_actions=approved,
        policy_denials=denied,
    )

