"""Run a reproducible command-line demonstration of RazRevRec."""

from __future__ import annotations

import argparse

from .audit import AuditTrail
from .evaluator import evaluate_batch
from .simulator import generate_transactions


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RazRevRec against a retry-only baseline.")
    parser.add_argument("--transactions", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260827)
    args = parser.parse_args()

    holdout = generate_transactions(args.transactions, args.seed, "holdout")
    audit = AuditTrail()
    baseline = evaluate_batch(holdout, "baseline")
    adaptive = evaluate_batch(holdout, "razrevrec", audit)
    valid, explanation = audit.verify()

    print("RazRevRec batch evaluation (synthetic held-out data)")
    print(f"Transactions: {args.transactions:,} | Revenue at risk: INR {adaptive.at_risk_amount:,.2f}")
    print(f"Retry-only baseline net recovery: INR {baseline.net_recovered_value:,.2f}")
    print(f"RazRevRec net recovery:          INR {adaptive.net_recovered_value:,.2f}")
    print(f"Net recovery lift:               INR {adaptive.net_recovered_value - baseline.net_recovered_value:,.2f}")
    print(f"RazRevRec customer contacts:     {adaptive.customer_contacts}")
    print(f"Policy denials (safety stops):   {adaptive.policy_denials}")
    print(f"Audit events: {len(audit.events):,} | {explanation if valid else 'FAILED: ' + explanation}")


if __name__ == "__main__":
    main()

