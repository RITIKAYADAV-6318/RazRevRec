"""Calibration metrics for executed recovery interventions."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

from .policy_guard import PolicyGuard, RecoveryCase
from .simulator import SimulatedTransaction, simulate_outcome
from .strategy import StrategyEngine, context_from_transaction


@dataclass(frozen=True, kw_only=True)
class CalibrationBin:
    bin_lower: float
    bin_upper: float
    mean_predicted: float
    observed_rate: float
    count: int


@dataclass(frozen=True, kw_only=True)
class CalibrationReport:
    bins: tuple[CalibrationBin, ...]
    expected_calibration_error: float
    naive_constant_ece: float
    evaluated_predictions: int


@dataclass(frozen=True, kw_only=True)
class PlattCalibrator:
    """One-dimensional post-hoc probability calibrator trained on a prior batch."""
    slope: float
    intercept: float

    def transform(self, probability: float) -> float:
        bounded = min(1 - 1e-6, max(1e-6, probability))
        logit = math.log(bounded / (1 - bounded))
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, self.slope * logit + self.intercept))))


def _executed_pairs(transactions: list[SimulatedTransaction]) -> list[tuple[float, bool]]:
    """Collect raw probability/outcome pairs only for actions actually executed."""
    guard = PolicyGuard()
    engine = StrategyEngine()
    pairs: list[tuple[float, bool]] = []
    for transaction in transactions:
        context = context_from_transaction(transaction)
        proposal = engine.choose(context, transaction.cohort)
        if proposal.recovery_probability <= 0:
            continue
        case = RecoveryCase(
            transaction_id=transaction.transaction_id,
            amount=transaction.amount,
            fraud_risk_score=transaction.fraud_risk_score,
            opted_out=transaction.opted_out,
            already_recovered=False,
            recovery_probability=proposal.recovery_probability,
            retry_attempt_number=transaction.retry_attempt_number,
            prior_notifications_sent=transaction.prior_notifications_sent,
        )
        if guard.evaluate(case, proposal.action).approved:
            pairs.append((proposal.recovery_probability, simulate_outcome(transaction, proposal.action).recovered))
    return pairs


def fit_platt_calibrator(transactions: list[SimulatedTransaction], iterations: int = 800, learning_rate: float = 0.08) -> PlattCalibrator:
    """Fit an out-of-model calibration layer on a separate, previously observed batch."""
    pairs = _executed_pairs(transactions)
    if not pairs:
        return PlattCalibrator(slope=1.0, intercept=0.0)
    features = [(math.log(min(1 - 1e-6, max(1e-6, p)) / (1 - min(1 - 1e-6, max(1e-6, p)))), float(y)) for p, y in pairs]
    slope, intercept = 1.0, 0.0
    for _ in range(iterations):
        grad_slope = grad_intercept = 0.0
        for feature, outcome in features:
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, slope * feature + intercept))))
            error = prediction - outcome
            grad_slope += error * feature
            grad_intercept += error
        slope -= learning_rate * grad_slope / len(features)
        intercept -= learning_rate * grad_intercept / len(features)
    return PlattCalibrator(slope=round(slope, 8), intercept=round(intercept, 8))


def compute_calibration(transactions: list[SimulatedTransaction], num_bins: int = 10, calibrator: PlattCalibrator | None = None) -> CalibrationReport:
    """Measure prediction reliability only where an intervention was executed."""
    if num_bins < 1:
        raise ValueError("num_bins must be positive")
    raw_pairs = _executed_pairs(transactions)
    pairs = [(calibrator.transform(predicted) if calibrator else predicted, recovered) for predicted, recovered in raw_pairs]

    width = 1.0 / num_bins
    bins: list[CalibrationBin] = []
    ece_numerator = 0.0
    for index in range(num_bins):
        lower, upper = index * width, (index + 1) * width
        members = [(predicted, recovered) for predicted, recovered in pairs if lower <= predicted < upper or (index == num_bins - 1 and predicted == upper)]
        if not members:
            continue
        count = len(members)
        mean_predicted = sum(predicted for predicted, _ in members) / count
        observed_rate = sum(recovered for _, recovered in members) / count
        bins.append(CalibrationBin(
            bin_lower=round(lower, 6),
            bin_upper=round(upper, 6),
            mean_predicted=round(mean_predicted, 6),
            observed_rate=round(observed_rate, 6),
            count=count,
        ))
        ece_numerator += count * abs(mean_predicted - observed_rate)

    total = len(pairs)
    naive_constant_ece = 0.0
    if total:
        observed_overall = sum(recovered for _, recovered in pairs) / total
        naive_constant_ece = abs(0.5 - observed_overall)
    return CalibrationReport(
        bins=tuple(bins),
        expected_calibration_error=round(ece_numerator / total, 6) if total else 0.0,
        naive_constant_ece=round(naive_constant_ece, 6),
        evaluated_predictions=total,
    )
