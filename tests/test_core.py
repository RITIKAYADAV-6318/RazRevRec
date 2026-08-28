import unittest

from src.cohorts import RecoveryAction
from src.audit import AuditTrail
from src.evaluator import evaluate_batch
from src.erv import expected_recovery_value
from src.policy_guard import PolicyGuard, RecoveryCase
from src.simulator import generate_transactions, recovery_probability, simulate_outcome
from src.strategy import StrategyEngine


class CoreTests(unittest.TestCase):
    def test_stop_has_zero_erv(self):
        self.assertEqual(expected_recovery_value(500, 0.9, RecoveryAction.STOP), 0.0)

    def test_guard_blocks_opted_out_customer_contact(self):
        case = RecoveryCase("tx-1", 8000, 0.1, True, False, 0.64, 0, 0)
        decision = PolicyGuard().evaluate(case, RecoveryAction.CUSTOMER_REMINDER)
        self.assertFalse(decision.approved)
        self.assertIn("opted out", decision.explanation)

    def test_simulator_is_repeatable(self):
        tx = generate_transactions(1, 42, "holdout")[0]
        self.assertEqual(simulate_outcome(tx, RecoveryAction.SMART_RETRY), simulate_outcome(tx, RecoveryAction.SMART_RETRY))
        self.assertEqual(generate_transactions(3, 42, "holdout"), generate_transactions(3, 42, "holdout"))

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
        case = RecoveryCase("tx-2", 8000, 0.05, False, False, 0.64, 0, 0)
        decision = StrategyEngine().choose(case, __import__("src.cohorts", fromlist=["FailureCohort"]).FailureCohort.INSUFFICIENT_FUNDS)
        self.assertEqual(decision.action, RecoveryAction.WAIT_AND_RETRY)

    def test_batch_evaluation_is_reproducible_and_auditable(self):
        batch = generate_transactions(25, 99, "holdout")
        trail = AuditTrail()
        first = evaluate_batch(batch, "razrevrec", trail)
        second = evaluate_batch(batch, "razrevrec")
        self.assertEqual(first, second)
        self.assertTrue(trail.verify()[0])
        self.assertGreater(first.at_risk_amount, 0)
