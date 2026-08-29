"""Export real pipeline results for the static RazRevRec dashboard."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .audit import AuditTrail
from .calibration import compute_calibration, fit_platt_calibrator
from .evaluator import evaluate_batch
from .simulator import generate_transactions


def _pick_hero_transaction(audit: AuditTrail) -> str | None:
    recovered = {
        event.payload["transaction_id"]
        for event in audit.events
        if event.event_type == "outcome" and event.payload.get("recovered") is True
    }
    return next((event.payload.get("transaction_id") for event in audit.events if event.payload.get("transaction_id") in recovered), None)


def _summary(event_type: str, payload: dict[str, object]) -> str:
    if event_type == "diagnosis":
        return f"Cohort: {payload.get('cohort')} · predicted recovery: {payload.get('probability')}"
    if event_type == "strategy":
        return f"Proposed action: {payload.get('proposed_action')}"
    if event_type == "policy_guard":
        return str(payload.get("explanation"))
    if event_type == "outcome":
        return f"Recovered: {payload.get('recovered')} · intervention cost: ₹{payload.get('cost')}"
    return str(payload)


def build_dashboard_payload(transactions: int = 2000, seed: int = 20260827) -> dict[str, object]:
    holdout = generate_transactions(transactions, seed, "holdout")
    training = generate_transactions(max(1000, transactions * 2), seed, "training")
    audit = AuditTrail()
    baseline = evaluate_batch(holdout, "baseline")
    razrevrec = evaluate_batch(holdout, "razrevrec", audit)
    calibrator = fit_platt_calibrator(training)
    raw_calibration = compute_calibration(holdout)
    calibration = compute_calibration(holdout, calibrator=calibrator)
    chain_valid, chain_explanation = audit.verify()
    hero_id = _pick_hero_transaction(audit)
    hero_events = [event for event in audit.events if event.payload.get("transaction_id") == hero_id]
    by_type = {event.event_type: event.payload for event in hero_events}

    return {
        "batch": {
            "transactions": transactions,
            "at_risk_amount": razrevrec.at_risk_amount,
            "baseline": asdict(baseline),
            "razrevrec": asdict(razrevrec),
            "net_lift": round(razrevrec.net_recovered_value - baseline.net_recovered_value, 2),
        },
        "calibration": {
            "evaluated_predictions": calibration.evaluated_predictions,
            "expected_calibration_error": calibration.expected_calibration_error,
            "raw_expected_calibration_error": raw_calibration.expected_calibration_error,
            "naive_constant_ece": raw_calibration.naive_constant_ece,
            "platt_slope": calibrator.slope,
            "platt_intercept": calibrator.intercept,
            "bins": [asdict(item) for item in calibration.bins],
        },
        "hero_case": {
            "transaction_id": hero_id,
            "cohort": by_type.get("diagnosis", {}).get("cohort"),
            "probability": by_type.get("diagnosis", {}).get("probability"),
            "action": by_type.get("strategy", {}).get("proposed_action"),
            "guard_explanation": by_type.get("policy_guard", {}).get("explanation"),
            "recovered": by_type.get("outcome", {}).get("recovered"),
        },
        "audit": {
            "chain_valid": chain_valid,
            "explanation": chain_explanation,
            "hero_events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "summary": _summary(event.event_type, event.payload),
                    "event_hash": event.event_hash[:12],
                }
                for event in hero_events
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export real RazRevRec metrics for the dashboard.")
    parser.add_argument("--transactions", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--out", type=Path, default=Path("public/dashboard_data.json"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build_dashboard_payload(args.transactions, args.seed), indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
