# RazRevRec

**Adaptive, policy-bounded recovery for failed SaaS subscription payments.**

For every failed payment, RazRevRec estimates recoverability, selects the
lowest-cost viable recovery action, enforces customer-safety rules, executes
a bounded recovery workflow, and proves recovered revenue against a naive
retry baseline with a full, hash-chained, tamper-evident audit trail.

Built for the Razorpay AI Buildathon (Revenue Recovery track).

---

## Headline numbers (real, reproducible - not asserted)

Run `python -m src.demo --transactions 2000 --seed 20260827` yourself to
reproduce this exact output:

```
Transactions: 2,000 | Revenue at risk: INR 20,310,419.29
Retry-only baseline net recovery: INR 1,415,417.52
RazRevRec net recovery:          INR 3,396,147.79
Net recovery lift:               INR 1,980,730.27
RazRevRec customer contacts:     275
Strategy-selected stops:         2
Policy Guard overrides:          1091
  - low_probability: 620
  - fraud_risk: 230
  - max_notifications: 126
  - max_retries: 99
  - opted_out: 18
Executed-action Brier score:      0.1779 (lower is better)
Naive constant-0.5 Brier score:   0.2500 (fixed baseline -- 0.25 for any binary outcome)
Audit events: 6,907 | Audit trail integrity verified.
```

**INR 19,80,730 recovered above baseline** on this batch, net of every
intervention's cost while sending 275 customer contacts (not 2,000),
and with the model beating a coin-flip guess on Brier score by a wide,
mathematically non-gameable margin.

---

## Architecture

```
                         +----------------------+
                         |  simulator.py        |
                         |  Synthetic, seeded   |
                         |  failed-payment      |
                         |  transactions        |
                         +----------+-----------+
                                    | observable fields only
                                    | (no oracle leakage)
                                    v
                         +----------------------+
                         |  strategy.py         |
                         |  StrategyEngine      |
                         |  Scores every action |
                         |  by Expected Recovery|
                         |  Value (erv.py),     |
                         |  proposes the best   |
                         +----------+-----------+
                                    | proposed action
                                    v
                         +----------------------+
                         |  policy_guard.py     |
                         |  PolicyGuard         |
                         |  ALWAYS evaluates,   |
                         |  even for STOP.      |
                         |  Idempotency, fraud, |
                         |  opt-out, retry/     |
                         |  notification caps.  |
                         +----------+-----------+
                                    | approved / denied (+ reason code)
                                    v
                         +----------------------+
                         |  evaluator.py        |
                         |  evaluate_batch()    |
                         |  Executes approved   |
                         |  actions, tallies    |
                         |  recovered revenue,  |
                         |  costs, denial       |
                         |  reasons             |
                         +----------+-----------+
                                    | every diagnosis/strategy/guard/
                                    | outcome event, in order
                                    v
                         +----------------------+
                         |  audit.py            |
                         |  AuditTrail          |
                         |  Hash-chained,       |
                         |  tamper-evident      |
                         |  event log           |
                         +----------+-----------+
                                    |
                    +---------------+---------------+
                    v                                v
         +----------------------+         +----------------------+
         |  calibration.py      |         |  dashboard/          |
         |  ECE + Brier score   |         |  index.html          |
         |  vs. a fixed naive   |         |  Single-file, zero-  |
         |  baseline            |         |  dependency dashboard|
         +----------------------+         |  with a real,        |
                                           |  browser-side        |
                                           |  SHA-256 tamper-     |
                                           |  evidence demo       |
                                           +----------------------+
```

**Key design properties, each backed by a test or benchmark, not just asserted:**

- **Deterministic.** Same seed -> same transactions, same decisions, same
  audit hashes, every time, on any machine (`tests/test_core.py`).
- **No oracle leakage.** The probability model only ever sees fields a real
  merchant could observe -- hidden fraud ground truth and the outcome-determining
  latent draw are never exposed to it (`test_observable_data_excludes_hidden_fields`).
- **The guard always evaluates, unconditionally** -- even when the strategy
  itself proposes to do nothing. This was a real bug, found and fixed (see
  [`WHAT_BROKE.md`](./WHAT_BROKE.md)).
- **Stateless, horizontally shardable.** `StrategyEngine` and `PolicyGuard`
  hold no mutable state across calls -- see "Scaling to Production" below.

---

## Repository structure

```
RazRevRec/
├── src/
│   ├── simulator.py               synthetic, seeded transaction generator
│   ├── cohorts.py                 failure cohorts, action costs, action sets
│   ├── strategy.py                StrategyEngine (ERV-based action scoring)
│   ├── erv.py                     Expected Recovery Value formula
│   ├── policy_guard.py            PolicyGuard (safety/compliance rules)
│   ├── evaluator.py               batch evaluation loop
│   ├── audit.py                   hash-chained audit trail
│   ├── calibration.py             ECE + Brier score vs. naive baseline
│   ├── demo.py                    CLI demo (the numbers above)
│   ├── dashboard_export.py        JSON export (used by test suite)
│   └── export_static_dashboard.py regenerates dashboard/index.html in place
├── dashboard/
│   └── index.html                 standalone, single-file dashboard
├── benchmarks/
│   └── throughput.py              decision-loop throughput benchmark
├── tests/
│   └── test_core.py               31 tests
├── WHAT_BROKE.md                  honest account of real bugs found & fixed
└── README.md                      this file
```

---

## Running it yourself

**Run the test suite:**
```powershell
python -m unittest discover -s tests -v
```
Expect: `Ran 31 tests ... OK`

**Run the reproducible batch demo:**
```powershell
python -m src.demo --transactions 2000 --seed 20260827
```
This compares RazRevRec against a fixed retry-only baseline: the baseline
proposes one immediate retry when a payment has fewer than two prior retry
attempts, otherwise it stops. Both strategies pass through the exact same
Policy Guard -- the baseline doesn't get an easier safety bar just because
it's dumber, or the "lift" number would partly just be measuring "unsafe
behavior is unsafe," not better decision-making.

**Run the throughput benchmark:**
```powershell
python -m benchmarks.throughput
```

**Open the dashboard:**
Just double-click `dashboard/index.html`, or open it in any browser -- it's
a single self-contained file with no build step, no server, and no external
dependency beyond an optional Google Fonts import (falls back gracefully
if unavailable). It includes:
- Batch metrics with a live RazRevRec-vs-baseline toggle
- A breakdown of *why* the Policy Guard blocks ~66% of proposals
- The calibration reliability chart and Brier score comparison
- Two real transactions, followed end to end, with a genuinely working
  SHA-256 tamper-evidence demo computed in your browser

**Regenerate the dashboard's data from a fresh pipeline run:**
```powershell
python -m src.export_static_dashboard
```
This edits `dashboard/index.html` in place, replacing only the data between
two marker comments -- never a manual copy-paste, and never a second file
that could fail to load.

---

## Scaling to Production

The core decision loop in `evaluate_batch` is deliberately simple: one pass
over transactions, constant work per transaction, no shared mutable state.
Run `python -m benchmarks.throughput` yourself to reproduce these numbers --
on this project's dev machine, single-core throughput measures at roughly
**45,000-58,000 transactions/sec**, flat across batch sizes from 1,000 to
50,000 (confirming O(n), not a hidden quadratic cost). Razorpay's ~35M
daily UPI transactions average to **~405/sec** -- so the decision logic
itself has roughly 100x headroom on a single core before it becomes the
bottleneck. That's expected: classifying a failure and picking an action
is cheap; it's everything *around* that decision that needs real
infrastructure at production scale.

**Where the real bottlenecks would be, and how we'd address each:**

| Concern | Current (demo) | Production path |
|---|---|---|
| **Audit trail storage** | In-memory Python list (`AuditTrail._events`), exported to JSON on demand | Append-only persisted log -- a write-ahead log or Kafka topic, with the same hash-chaining logic applied to persisted records instead of an in-memory list. `verify()` becomes a batch or streaming job over the persisted chain. |
| **Horizontal scale** | Single-process, sequential loop | `StrategyEngine.choose()` and `PolicyGuard.evaluate()` are already stateless per transaction -- no shared mutable state, config set once at init. Transactions can be sharded across workers (e.g. by `transaction_id` hash) and processed independently in parallel with no changes to the decision logic itself. |
| **Recovery probability model** | Hand-tuned multiplicative formula in `estimate_recovery_probability`, calibrated post-hoc | Retrain as a model on real historical recovery outcomes, refreshed on a schedule as failure patterns shift. `calibration.py` is designed to sit in front of whatever produces raw probabilities, so this swap doesn't require touching strategy or guard logic. |
| **External calls (retries, notifications)** | Simulated in `simulator.py` | Real integrations (payment retry APIs, SMS/WhatsApp) are I/O-bound and will dominate latency long before the decision logic does -- these should be async/queued, with the Policy Guard's approval still gating dispatch synchronously so nothing fires without passing safety checks first. |

The decision architecture itself -- guard-always-evaluates, stateless
engine, audit-append pattern -- carries over unchanged from demo to
production; only the backing storage and execution model need to grow up.

---

## What broke, and how we know it's fixed

See [`WHAT_BROKE.md`](./WHAT_BROKE.md) for a specific, honest account of
every real bug found during development -- including partial fixes that
looked complete but weren't, and how each fix was verified (not just
claimed) against the actual code.

---

## Data note

All numbers above come from a **synthetic, seeded dataset** -- 2,000
simulated payment failures across realistic cohorts (insufficient funds,
expired cards, 3DS auth failures, processor timeouts, checkout abandonment,
fraud declines), not live Razorpay transaction data. This is standard and
expected for a buildathon submission; every number is reproducible by
anyone who clones this repo and runs the commands above.

# Author - 
# Ritika Yadav [Linkedin](https://www.linkedin.com/in/ritika-yadav-189b9137a/?lipi=urn%3Ali%3Apage%3Ad_flagship3_profile_view_base_contact_details%3BAuCWcsiJR4%2BnaBMrs133AA%3D%3D) [email](ritika170btcse25@igdtuw.ac.in)
