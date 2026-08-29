"""Reproducible batch evaluation for the baseline and RazRevRec strategies."""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditTrail
from .cohorts import RecoveryAction
from .policy_guard import PolicyGuard, RecoveryCase
from .simulator import SimulatedTransaction, simulate_outcome
from .strategy import StrategyEngine, context_from_transaction, estimate_recovery_probability


@dataclass(frozen=True)
class EvaluationMetrics:
    transactions: int
    at_risk_amount: float
    recovered_amount: float
    intervention_cost: float
    net_recovered_value: float
    customer_contacts: int
    approved_actions: int
    strategy_stops: int
    guard_overrides: int


def _case_for_guard(transaction: SimulatedTransaction, probability: float, already_recovered: bool = False) -> RecoveryCase:
    return RecoveryCase(
        transaction_id=transaction.transaction_id,
        amount=transaction.amount,
        fraud_risk_score=transaction.fraud_risk_score,
        opted_out=transaction.opted_out,
        already_recovered=already_recovered,
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
    already_recovered_ids: frozenset[str] = frozenset(),
) -> EvaluationMetrics:
    """Evaluate one strategy on an identical synthetic holdout batch."""
    if mode not in {"razrevrec", "baseline"}:
        raise ValueError("mode must be 'razrevrec' or 'baseline'")
    guard = PolicyGuard()
    engine = StrategyEngine()
    at_risk = recovered = cost = 0.0
    contacts = approved = strategy_stops = guard_overrides = 0
    for transaction in transactions:
        at_risk += transaction.amount
        context = context_from_transaction(transaction)
        if mode == "razrevrec":
            proposal = engine.choose(context, transaction.cohort)
            action = proposal.action
            probability = proposal.recovery_probability
        else:
            action = _baseline_action(transaction)
            probability = estimate_recovery_probability(context, transaction.cohort, action)
        if action is RecoveryAction.STOP:
            strategy_stops += 1
            if audit_trail:
                audit_trail.append("diagnosis", {"transaction_id": transaction.transaction_id, "cohort": transaction.cohort.value, "probability": probability})
                audit_trail.append("strategy", {"transaction_id": transaction.transaction_id, "mode": mode, "proposed_action": action.value})
                audit_trail.append("policy_guard", {"transaction_id": transaction.transaction_id, "approved": False, "explanation": "No recovery action proposed: strategy selected STOP."})
            continue
        case = _case_for_guard(transaction, probability, transaction.transaction_id in already_recovered_ids)
        decision = guard.evaluate(case, action)
        if audit_trail:
            audit_trail.append("diagnosis", {"transaction_id": transaction.transaction_id, "cohort": transaction.cohort.value, "probability": probability})
            audit_trail.append("strategy", {"transaction_id": transaction.transaction_id, "mode": mode, "proposed_action": action.value})
            audit_trail.append("policy_guard", {"transaction_id": transaction.transaction_id, "approved": decision.approved, "explanation": decision.explanation})
        if not decision.approved:
            guard_overrides += 1
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
        strategy_stops=strategy_stops,
        guard_overrides=guard_overrides,
    )
