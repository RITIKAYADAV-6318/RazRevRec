# RazRevRec

Adaptive, policy-bounded recovery for failed SaaS subscription payments.

## Run the core tests

```powershell
python -m unittest discover -s tests -v
```

## Run a reproducible batch demonstration

```powershell
python -m src.demo --transactions 2000 --seed 20260827
```

The comparison is against a fixed retry-only baseline: it proposes one immediate retry when the payment has fewer than two prior retry attempts; otherwise it stops. Both strategies pass through the same non-negotiable Policy Guard. Customer contacts are reported as an independent customer-impact metric; a retry-only baseline has no contacts, so it would be misleading to claim that RazRevRec sends fewer than that baseline.

## Scaling to Production

The core decision loop in `evaluate_batch` is deliberately simple: one pass
over transactions, constant work per transaction, no shared mutable state.
Run `python -m benchmarks.throughput` yourself to reproduce these numbers —
on this project's dev machine, single-core throughput measures at roughly
**45,000-58,000 transactions/sec**, flat across batch sizes from 1,000 to
50,000 (confirming O(n), not a hidden quadratic cost). Razorpay's ~35M
daily UPI transactions average to **~405/sec** — so the decision logic
itself has roughly 100x headroom on a single core before it becomes the
bottleneck. That's expected: classifying a failure and picking an action
is cheap; it's everything *around* that decision that needs real
infrastructure at production scale.

**Where the real bottlenecks would be, and how we'd address each:**

| Concern | Current (demo) | Production path |
|---|---|---|
| **Audit trail storage** | In-memory Python list (`AuditTrail._events`), exported to JSON on demand | Append-only persisted log — a write-ahead log or Kafka topic, with the same hash-chaining logic applied to persisted records instead of an in-memory list. `verify()` becomes a batch or streaming job over the persisted chain. |
| **Horizontal scale** | Single-process, sequential loop | `StrategyEngine.choose()` and `PolicyGuard.evaluate()` are already stateless per transaction — no shared mutable state, config set once at init. Transactions can be sharded across workers (e.g. by `transaction_id` hash) and processed independently in parallel with no changes to the decision logic itself. |
| **Recovery probability model** | Hand-tuned multiplicative formula in `estimate_recovery_probability`, calibrated post-hoc | Retrain as a model on real historical recovery outcomes, refreshed on a schedule as failure patterns shift. `calibration.py` is designed to sit in front of whatever produces raw probabilities, so this swap doesn't require touching strategy or guard logic. |
| **External calls (retries, notifications)** | Simulated in `simulator.py` | Real integrations (payment retry APIs, SMS/WhatsApp) are I/O-bound and will dominate latency long before the decision logic does — these should be async/queued, with the Policy Guard's approval still gating dispatch synchronously so nothing fires without passing safety checks first. |

The decision architecture itself — guard-always-evaluates, stateless
engine, audit-append pattern — carries over unchanged from demo to
production; only the backing storage and execution model need to grow up.