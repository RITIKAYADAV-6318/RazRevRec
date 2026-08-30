import subprocess
import sys
import unittest
from unittest.mock import patch

from src.cohorts import FailureCohort, RecoveryAction
from src.audit import AuditTrail
from src.calibration import compute_calibration, fit_platt_calibrator
from src.dashboard_export import build_dashboard_payload
from src.evaluator import evaluate_batch
from src.erv import expected_recovery_value
from src.policy_guard import PolicyGuard, RecoveryCase
from src.simulator import generate_transactions, recovery_probability, simulate_outcome
from src.strategy import StrategyContext, StrategyDecision, StrategyEngine


class CoreTests(unittest.TestCase):
    def test_stop_has_zero_erv(self):
        self.assertEqual(expected_recovery_value(500, 0.9, RecoveryAction.STOP), 0.0)

    def test_fatigue_penalty_reduces_contact_erv(self):
        fresh = expected_recovery_value(500, 0.5, RecoveryAction.CUSTOMER_REMINDER, 0)
        fatigued = expected_recovery_value(500, 0.5, RecoveryAction.CUSTOMER_REMINDER, 2)
        self.assertGreater(fresh, fatigued)

    def test_fraud_threshold_is_inclusive(self):
        case = RecoveryCase(transaction_id="tx-1", amount=8000, fraud_risk_score=0.70, opted_out=False, already_recovered=False, recovery_probability=0.64, retry_attempt_number=0, prior_notifications_sent=0)
        self.assertTrue(PolicyGuard().evaluate(case, RecoveryAction.SMART_RETRY).approved)

    def test_guard_blocks_opted_out_customer_contact(self):
        case = RecoveryCase(transaction_id="tx-1", amount=8000, fraud_risk_score=0.1, opted_out=True, already_recovered=False, recovery_probability=0.64, retry_attempt_number=0, prior_notifications_sent=0)
        decision = PolicyGuard().evaluate(case, RecoveryAction.CUSTOMER_REMINDER)
        self.assertFalse(decision.approved)
        self.assertIn("opted out", decision.explanation)

    def test_guard_blocks_already_recovered_payment(self):
        case = RecoveryCase(transaction_id="tx-1", amount=8000, fraud_risk_score=0.1, opted_out=False, already_recovered=True, recovery_probability=0.64, retry_attempt_number=0, prior_notifications_sent=0)
        decision = PolicyGuard().evaluate(case, RecoveryAction.SMART_RETRY)
        self.assertFalse(decision.approved)
        self.assertIn("idempotency", decision.explanation)

    def test_simulator_is_repeatable(self):
        tx = generate_transactions(1, 42, "holdout")[0]
        self.assertEqual(simulate_outcome(tx, RecoveryAction.SMART_RETRY), simulate_outcome(tx, RecoveryAction.SMART_RETRY))
        self.assertEqual(generate_transactions(3, 42, "holdout"), generate_transactions(3, 42, "holdout"))

    def test_simulator_is_repeatable_across_processes(self):
        script = "from src.simulator import generate_transactions; print(generate_transactions(1, 42, 'holdout')[0])"
        first = subprocess.check_output([sys.executable, "-c", script], text=True)
        second = subprocess.check_output([sys.executable, "-c", script], text=True)
        self.assertEqual(first, second)

    def test_observable_data_excludes_hidden_fields(self):
        observed = generate_transactions(1, 42, "train")[0].observable_dict()
        self.assertNotIn("_fraud_ground_truth", observed)
        self.assertNotIn("_latent_draw", observed)

    def test_train_and_holdout_ids_are_disjoint(self):
        train_ids = {tx.transaction_id for tx in generate_transactions(5, 1, "train")}
        holdout_ids = {tx.transaction_id for tx in generate_transactions(5, 1, "holdout")}
        self.assertTrue(train_ids.isdisjoint(holdout_ids))

    def test_shared_draw_enforces_monotonic_outcomes(self):
        for transaction in generate_transactions(100, 10, "monotonic"):
            candidates = [(recovery_probability(transaction, action), simulate_outcome(transaction, action).recovered) for action in RecoveryAction]
            for low_probability, low_recovered in candidates:
                for high_probability, high_recovered in candidates:
                    if high_probability >= low_probability and low_recovered:
                        self.assertTrue(high_recovered)

    def test_hash_chain_detects_tampering(self):
        trail = AuditTrail()
        trail.append("diagnosis", {"transaction_id": "tx-1", "cohort": "insufficient_funds"}, "2026-08-27T10:00:00+00:00")
        trail.append("outcome", {"recovered": True}, "2026-08-27T10:01:00+00:00")
        self.assertTrue(trail.verify()[0])
        trail._events[0].payload["cohort"] = "risk_fraud_decline"  # test deliberate tampering
        self.assertFalse(trail.verify()[0])

    def test_strategy_prefers_delayed_retry_for_insufficient_funds(self):
        context = StrategyContext(transaction_id="tx-2", amount=8000, fraud_risk_score=0.05, opted_out=False, retry_attempt_number=0, prior_notifications_sent=0, customer_history_score=0.8)
        decision = StrategyEngine().choose(context, FailureCohort.INSUFFICIENT_FUNDS)
        self.assertEqual(decision.action, RecoveryAction.WAIT_AND_RETRY)

    def test_batch_evaluation_is_reproducible_and_auditable(self):
        batch = generate_transactions(25, 99, "holdout")
        trail = AuditTrail()
        first = evaluate_batch(batch, "razrevrec", trail)
        second = evaluate_batch(batch, "razrevrec")
        self.assertEqual(first, second)
        self.assertTrue(trail.verify()[0])
        self.assertGreater(first.at_risk_amount, 0)

    def test_batch_exercises_idempotency_stop(self):
        batch = generate_transactions(3, 99, "holdout")
        trail = AuditTrail()
        evaluate_batch(batch, "razrevrec", trail, frozenset({batch[0].transaction_id}))
        decisions = [event.payload["explanation"] for event in trail.events if event.event_type == "policy_guard"]
        self.assertTrue(any("idempotency" in explanation for explanation in decisions))

    def test_stop_proposal_still_checked_for_idempotency(self):
        # Regression test: even when the strategy independently proposes STOP,
        # the Policy Guard must still evaluate — its idempotency/fraud/opt-out
        # checks are safety rules, not strategy rules, and must never be
        # skippable just because the strategy also happened to say "stop".
        batch = generate_transactions(1, 7, "holdout")
        forced_stop = StrategyDecision(RecoveryAction.STOP, 0.0, 0.0, "forced for test")
        trail = AuditTrail()
        with patch("src.evaluator.StrategyEngine.choose", return_value=forced_stop):
            evaluate_batch(batch, "razrevrec", trail, frozenset({batch[0].transaction_id}))
        guard_events = [event.payload for event in trail.events if event.event_type == "policy_guard"]
        self.assertEqual(len(guard_events), 1)
        self.assertIn("idempotency", guard_events[0]["explanation"])
        self.assertNotIn("strategy selected STOP", guard_events[0]["explanation"])

    def test_audit_approved_field_matches_actual_execution(self):
        """Regression test: the policy_guard audit event's 'approved' field must
        never contradict whether the transaction was actually executed. This
        catches a real bug where 'approved' was hardcoded to False for every
        transaction, even ones with a completed outcome event."""
        batch = generate_transactions(30, 7, "audit_consistency_check")
        trail = AuditTrail()
        evaluate_batch(batch, "razrevrec", trail)
        guard_events = {e.payload["transaction_id"]: e.payload for e in trail.events if e.event_type == "policy_guard"}
        outcome_ids = {e.payload["transaction_id"] for e in trail.events if e.event_type == "outcome"}
        for tid in outcome_ids:
            self.assertTrue(
                guard_events[tid]["approved"],
                f"Transaction {tid} has an outcome event (was executed) but its "
                f"policy_guard audit entry says approved=False"
            )

    def test_calibration_is_deterministic(self):
        batch = generate_transactions(200, 42, "holdout")
        self.assertEqual(compute_calibration(batch), compute_calibration(batch))

    def test_post_hoc_calibrator_is_deterministic(self):
        training = generate_transactions(500, 8, "training")
        self.assertEqual(fit_platt_calibrator(training), fit_platt_calibrator(training))

    def test_calibration_excludes_denied_and_stopped_transactions(self):
        batch = generate_transactions(200, 42, "holdout")
        report = compute_calibration(batch)
        self.assertLess(report.evaluated_predictions, len(batch))
        self.assertGreater(report.evaluated_predictions, 0)

    def test_sparse_calibration_bins_are_safe(self):
        report = compute_calibration(generate_transactions(2, 42, "holdout"), num_bins=10)
        self.assertGreaterEqual(report.expected_calibration_error, 0.0)
        self.assertLessEqual(report.expected_calibration_error, 1.0)

    def test_dashboard_export_uses_verified_audit_events(self):
        payload = build_dashboard_payload(100, 42)
        self.assertTrue(payload["audit"]["chain_valid"])
        self.assertTrue(payload["hero_case"]["transaction_id"])
        self.assertTrue(payload["audit"]["hero_events"])