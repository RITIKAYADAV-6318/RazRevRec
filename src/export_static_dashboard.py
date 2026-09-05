"""Regenerate the DATA block inside dashboard/index.html from a real pipeline run.

Usage:
    python -m src.export_static_dashboard

This edits dashboard/index.html IN PLACE, replacing only the content between
the RAZREVREC_DATA_START / RAZREVREC_DATA_END markers -- everything else in
the file (styles, logic, markup) is left untouched. There is no separate
data file: a single self-contained HTML file is what makes the dashboard
work when opened directly (file://), previewed in Claude, or served from
anywhere, with no second file that can fail to load.

If the markers aren't found, this fails loudly with a clear error rather
than silently corrupting the file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .audit import AuditTrail, AuditEvent
from .calibration import compute_calibration
from .evaluator import evaluate_batch, EvaluationMetrics
from .simulator import generate_transactions

START_MARKER = "/* RAZREVREC_DATA_START"
END_MARKER = "/* RAZREVREC_DATA_END */"


def _metrics_for_js(metrics: EvaluationMetrics) -> dict[str, object]:
    return {
        "net_recovered_value": metrics.net_recovered_value,
        "customer_contacts": metrics.customer_contacts,
        "approved_actions": metrics.approved_actions,
        "strategy_stops": metrics.strategy_stops,
        "guard_overrides": metrics.guard_overrides,
        "guard_denial_reasons": metrics.guard_denial_reasons,
    }


def _full_events_for(audit: AuditTrail, transaction_id: str) -> list[AuditEvent]:
    return [event for event in audit.events if event.payload.get("transaction_id") == transaction_id]


def _event_for_js(event: AuditEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "event_type": event.event_type,
        "payload": event.payload,
        "previous_hash": event.previous_hash,
        "event_hash": event.event_hash,
    }


def _pick_recovered_case(audit: AuditTrail) -> str | None:
    for event in audit.events:
        if event.event_type == "outcome" and event.payload.get("recovered") is True:
            return event.payload["transaction_id"]
    return None


def _pick_denied_case(audit: AuditTrail) -> str | None:
    fraud_denial = None
    any_denial = None
    for event in audit.events:
        if event.event_type != "policy_guard" or event.payload.get("approved") is not False:
            continue
        tid = event.payload["transaction_id"]
        full_events = _full_events_for(audit, tid)
        if any(e.event_type == "outcome" for e in full_events):
            continue
        if any_denial is None:
            any_denial = tid
        if event.payload.get("reason") == "fraud_risk" and fraud_denial is None:
            fraud_denial = tid
            break
    return fraud_denial or any_denial


def _narrative_for(audit: AuditTrail, transaction_id: str, kind: str) -> tuple[str, str]:
    events = _full_events_for(audit, transaction_id)
    by_type = {e.event_type: e.payload for e in events}
    cohort = by_type["diagnosis"]["cohort"].replace("_", " ")
    probability_pct = round(by_type["diagnosis"]["probability"] * 100)
    proposed = by_type["strategy"]["proposed_action"].replace("_", " ")
    if kind == "recovered":
        title = "One case, followed end to end"
        narrative = f"{cohort.capitalize()}, {probability_pct}% predicted recovery chance. The guard approved {proposed}. It worked."
    else:
        reason = by_type["policy_guard"].get("reason", "policy")
        title = "One case, correctly refused"
        narrative = (
            f"{cohort.capitalize()}, {probability_pct}% predicted recovery chance. "
            f"The strategy proposed {proposed} anyway — the guard independently blocked it "
            f"for {reason.replace('_', ' ')} before anything executed."
        )
    return title, narrative


def build_data_dict(transactions: int, seed: int) -> dict[str, object]:
    holdout = generate_transactions(transactions, seed, "holdout")
    audit = AuditTrail()
    baseline = evaluate_batch(holdout, "baseline")
    razrevrec = evaluate_batch(holdout, "razrevrec", audit)
    calibration = compute_calibration(holdout)
    chain_valid, _ = audit.verify()

    recovered_id = _pick_recovered_case(audit)
    denied_id = _pick_denied_case(audit)
    if recovered_id is None or denied_id is None:
        raise RuntimeError(
            f"Could not find both a recovered case and a denied case "
            f"(recovered_id={recovered_id}, denied_id={denied_id}). Try a different seed."
        )

    recovered_title, recovered_narrative = _narrative_for(audit, recovered_id, "recovered")
    denied_title, denied_narrative = _narrative_for(audit, denied_id, "denied")

    return {
        "batch": {
            "transactions": transactions,
            "at_risk_amount": razrevrec.at_risk_amount,
            "baseline": _metrics_for_js(baseline),
            "razrevrec": _metrics_for_js(razrevrec),
            "net_lift": round(razrevrec.net_recovered_value - baseline.net_recovered_value, 2),
        },
        "calibration": {
            "evaluated_predictions": calibration.evaluated_predictions,
            "brier_score": calibration.brier_score,
            "naive_constant_brier": calibration.naive_constant_brier,
            "bins": [
                {"bin_lower": b.bin_lower, "mean_predicted": b.mean_predicted, "observed_rate": b.observed_rate, "count": b.count}
                for b in calibration.bins
            ],
        },
        "auditSummary": {"totalEvents": len(audit.events), "chainValid": chain_valid},
        "cases": [
            {
                "id": "recovered",
                "badgeLabel": "RECOVERED",
                "badgeVar": "--recovered",
                "title": recovered_title,
                "transactionId": recovered_id,
                "narrative": recovered_narrative,
                "events": [_event_for_js(e) for e in _full_events_for(audit, recovered_id)],
            },
            {
                "id": "denied",
                "badgeLabel": "STOPPED",
                "badgeVar": "--denied",
                "title": denied_title,
                "transactionId": denied_id,
                "narrative": denied_narrative,
                "events": [_event_for_js(e) for e in _full_events_for(audit, denied_id)],
            },
        ],
    }


def regenerate(dashboard_path: Path, transactions: int, seed: int) -> None:
    html = dashboard_path.read_text(encoding="utf-8")
    if START_MARKER not in html or END_MARKER not in html:
        raise RuntimeError(
            f"Could not find RAZREVREC_DATA_START/END markers in {dashboard_path}. "
            f"Refusing to guess where to insert data -- add the markers first."
        )

    data = build_data_dict(transactions, seed)
    new_data_line = "const DATA = " + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";"

    pattern = re.compile(
        r"(/\* RAZREVREC_DATA_START.*?\*/\n).*?(\n/\* RAZREVREC_DATA_END \*/)",
        re.DOTALL,
    )
    updated, count = pattern.subn(lambda m: m.group(1) + new_data_line + m.group(2), html, count=1)
    if count != 1:
        raise RuntimeError(f"Expected exactly 1 marker block, found {count}. Aborting without writing.")

    dashboard_path.write_text(updated, encoding="utf-8")
    print(f"Regenerated {dashboard_path} in place ({transactions} transactions, seed {seed}).")
    print(f"Recovered case: {data['cases'][0]['transactionId']} | Denied case: {data['cases'][1]['transactionId']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate dashboard/index.html's embedded DATA in place")
    parser.add_argument("--transactions", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--dashboard", type=Path, default=Path("dashboard/index.html"))
    args = parser.parse_args()
    regenerate(args.dashboard, args.transactions, args.seed)


if __name__ == "__main__":
    main()