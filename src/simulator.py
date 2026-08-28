"""Deterministic potential-outcome simulator used for reproducible evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random

from .cohorts import ACTION_COSTS, COHORTS, FailureCohort, RecoveryAction


def _stable_unit_interval(*parts: object) -> float:
    raw = "|".join(map(str, parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") / 2**64


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-25.0, min(25.0, value))))


@dataclass(frozen=True)
class SimulatedTransaction:
    transaction_id: str
    amount: float
    cohort: FailureCohort
    retry_attempt_number: int
    prior_notifications_sent: int
    customer_history_score: float
    opted_out: bool
    fraud_risk_score: float
    hour_of_day: int
    # Simulator-only fields: never exposed to the decision model.
    _fraud_ground_truth: float
    _latent_draw: float

    def observable_dict(self) -> dict[str, object]:
        return {
            "transaction_id": self.transaction_id,
            "amount": self.amount,
            "cohort": self.cohort.value,
            "retry_attempt_number": self.retry_attempt_number,
            "prior_notifications_sent": self.prior_notifications_sent,
            "customer_history_score": self.customer_history_score,
            "opted_out": self.opted_out,
            "fraud_risk_score": self.fraud_risk_score,
            "hour_of_day": self.hour_of_day,
        }


@dataclass(frozen=True)
class SimulatedOutcome:
    action: RecoveryAction
    recovery_probability: float
    recovered: bool
    intervention_cost: float


ACTION_COHORT_EFFECT: dict[RecoveryAction, dict[FailureCohort, float]] = {
    RecoveryAction.SMART_RETRY: {
        FailureCohort.TEMPORARY_BANK_FAILURE: 1.25, FailureCohort.PROCESSOR_TIMEOUT: 1.25,
        FailureCohort.INSUFFICIENT_FUNDS: 0.80, FailureCohort.THREE_DS_AUTH_FAILURE: 0.45,
    },
    RecoveryAction.WAIT_AND_RETRY: {
        FailureCohort.INSUFFICIENT_FUNDS: 1.45, FailureCohort.TEMPORARY_BANK_FAILURE: 0.95,
        FailureCohort.PROCESSOR_TIMEOUT: 0.75,
    },
    RecoveryAction.PAYMENT_METHOD_UPDATE: {
        FailureCohort.EXPIRED_CARD: 1.55, FailureCohort.INVALID_CARD: 1.30,
    },
    RecoveryAction.CUSTOMER_REMINDER: {
        FailureCohort.THREE_DS_AUTH_FAILURE: 1.35, FailureCohort.CHECKOUT_ABANDONED: 1.50,
        FailureCohort.INSUFFICIENT_FUNDS: 0.80,
    },
    RecoveryAction.ESCALATE: {FailureCohort.RISK_FRAUD_DECLINE: 0.20},
    RecoveryAction.STOP: {},
}


def _logit(probability: float) -> float:
    probability = min(1 - 1e-6, max(1e-6, probability))
    return math.log(probability / (1 - probability))


def recovery_probability(transaction: SimulatedTransaction, action: RecoveryAction) -> float:
    """Potential recovery probability conditioned on the chosen action."""
    if action is RecoveryAction.STOP or transaction.opted_out and action in {RecoveryAction.CUSTOMER_REMINDER, RecoveryAction.PAYMENT_METHOD_UPDATE}:
        return 0.0
    base = COHORTS[transaction.cohort].base_recovery_probability
    action_multiplier = ACTION_COHORT_EFFECT.get(action, {}).get(transaction.cohort, 0.20)
    action_multiplier = max(action_multiplier, 0.02)
    score = _logit(base) + math.log(action_multiplier)
    score += 0.85 * (transaction.customer_history_score - 0.5)
    score -= 0.35 * transaction.retry_attempt_number
    score -= 1.6 * transaction._fraud_ground_truth
    score -= 0.18 * transaction.prior_notifications_sent if action in {RecoveryAction.CUSTOMER_REMINDER, RecoveryAction.PAYMENT_METHOD_UPDATE} else 0.0
    return round(_sigmoid(score), 6)


def simulate_outcome(transaction: SimulatedTransaction, action: RecoveryAction) -> SimulatedOutcome:
    probability = recovery_probability(transaction, action)
    return SimulatedOutcome(action, probability, transaction._latent_draw < probability, ACTION_COSTS[action])


def generate_transactions(count: int, experiment_seed: int, batch_name: str) -> list[SimulatedTransaction]:
    if count < 1:
        raise ValueError("count must be positive")
    rng = random.Random(f"{experiment_seed}:{batch_name}")
    cohorts = list(FailureCohort)
    transactions: list[SimulatedTransaction] = []
    for index in range(count):
        transaction_id = f"{batch_name}-{index:05d}"
        fraud_truth = rng.random()
        observable_score = min(0.99, max(0.01, fraud_truth * 0.78 + rng.uniform(-0.18, 0.18)))
        transactions.append(SimulatedTransaction(
            transaction_id=transaction_id,
            amount=round(rng.uniform(199, 20_000), 2),
            cohort=rng.choice(cohorts),
            retry_attempt_number=rng.randrange(0, 4),
            prior_notifications_sent=rng.randrange(0, 3),
            customer_history_score=round(rng.random(), 4),
            opted_out=rng.random() < 0.06,
            fraud_risk_score=round(observable_score, 4),
            hour_of_day=rng.randrange(0, 24),
            _fraud_ground_truth=round(fraud_truth, 6),
            _latent_draw=_stable_unit_interval(experiment_seed, transaction_id, "potential-outcome"),
        ))
    return transactions

