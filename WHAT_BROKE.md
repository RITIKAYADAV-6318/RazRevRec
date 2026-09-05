# What broke, and how we know it's fixed

This document is a specific, honest account of the real problems found during
development not a polished highlight reel. Several of these issues were
caught only after a partial or seemingly-complete fix already existed, and a
closer, independent check turned up something the first pass missed. We
think that trail is itself worth showing: it demonstrates a real
verification discipline checking every fix against the actual running
code, not trusting that a prior pass got it right rather than a
suspiciously clean "we never made mistakes" narrative.

Every fix below has a corresponding regression test in `tests/test_core.py`
that was verified, at the time, to **fail against the pre-fix code and pass
against the fix** — not just added and left unverified.

---

## 1. Counterfactual consistency in the simulator (design-stage correction)

**The risk:** the outcome simulator needs to answer "would this specific
transaction have recovered, under this specific action?" for many different
actions on the same transaction, so that RazRevRec and the baseline can be
compared fairly on identical underlying reality. An early instinct would be
to independently randomize each action's outcome per transaction but that
creates an incoherent counterfactual: a lower-probability action could
"randomly" succeed while a higher-probability action for the *same*
transaction "randomly" fails, which is not how a single real-world event
actually works, and would corrupt any fair comparison between strategies.

**The fix, verified in the current code:** each transaction gets exactly
**one hidden `_latent_draw` value** (`simulator.py`), and any action's outcome
is simply `latent_draw < probability_of_that_action`. This guarantees
monotonicity: if a lower-probability action would have recovered the payment,
every higher-probability action for that same transaction must also recover
it. Verified by `test_shared_draw_enforces_monotonic_outcomes`, which checks
this property across 100 transactions and all possible actions.

---

## 2. STOP silently skipped the Policy Guard entirely

**What was wrong:** in `evaluate_batch`, the original loop structure was:

```python
if action is RecoveryAction.STOP:
    strategy_stops += 1
    continue          # <-- guard.evaluate() never runs
case = _case_for_guard(...)
decision = guard.evaluate(case, action)
```

Whenever the strategy itself proposed `STOP`, the loop moved on before the
Policy Guard ever ran meaning idempotency, fraud, and opt-out checks were
silently skipped for that transaction.

**Why it mattered:** if a transaction was already recovered (idempotency
should block any further action, including a proposed `STOP`) but the
strategy happened to independently propose `STOP` for an unrelated reason,
the guard's real, safety-critical reason never got a chance to fire. The
audit trail would then record a misleading reason ("strategy selected STOP")
for what was actually a case that should never be touched again.

**The fix:** the guard now evaluates unconditionally, for every transaction,
before any STOP/approve/deny classification happens.

**How we know it's fixed:** `test_stop_proposal_still_checked_for_idempotency`
forces a strategy to propose `STOP` on an already-recovered transaction
(via a mocked `StrategyEngine.choose`, since natural random data almost never
produces `STOP`) and asserts the audit trail correctly attributes the
denial to idempotency, not to "strategy selected STOP." Verified to fail
against the pre-fix code and pass against the fix.

---

## 3. A regression introduced while fixing #2: `approved` hardcoded to `False`

**What happened:** while restructuring the loop to fix #2, the audit-logging
line for the `STOP` branch was written as:

```python
audit_trail.append("policy_guard", {..., "approved": False, "explanation": explanation})
```

`approved` was hardcoded to `False` regardless of what `decision.approved`
actually was. Every transaction that was genuinely approved and executed was
logged with a **self-contradictory** audit entry: `approved: false` sitting
right next to explanation text reading `"Approved: proposed action satisfies
all deterministic policy rules."`

**Why this was worse than bug #2, not better:** #2 produced a wrong *reason*
for a correct outcome. This produced a record that contradicts itself on its
face no tampering required to notice something is broken, just reading the
exported JSON. Confirmed empirically: **11 out of 11** executed transactions
in one test batch had this contradiction before the fix.

**The fix:** removed the special-case branching entirely. The guard's own
`approved` and `explanation` fields are now recorded verbatim, with no
transformation:

```python
audit_trail.append("policy_guard", {"transaction_id": ..., "approved": decision.approved, "explanation": decision.explanation})
```

**How we know it's fixed:** `test_audit_approved_field_matches_actual_execution`
asserts every transaction with a completed `outcome` event has
`approved: true` in its `policy_guard` event. Verified to fail against the
intermediate (buggy) code and pass against the fix.

---

## 4. `ESCALATE` was structurally guaranteed to be denied 100% of the time

**What was wrong:** `ESCALATE` exists specifically as the human-in-the-loop
fallback for cases too risky or uncertain for automation it's the
designated action for the fraud-decline cohort, and the strategy engine only
ever proposes it for low-probability cases. But the Policy Guard applied its
universal `fraud_risk_score > threshold` and `recovery_probability < minimum`
checks to `ESCALATE` the same as any automated action. Both checks exist to
block *automated* execution under exactly those conditions but those are
precisely the conditions that cause `ESCALATE` to be proposed in the first
place. The result: **every single escalation was denied, for the exact
reason it was escalated.**

**Why it mattered:** the brief explicitly names "compliant escalation" as a
scoring criterion. Measured on the real batch: 223 of 223 proposed
escalations were denied 206 by the probability floor, 17 by the fraud
threshold making the escalation path permanently dead code.

**This was fixed in two passes, and the first pass was incomplete:**

- **Pass 1** exempted `ESCALATE` only from the probability check. This
  unblocked 206 of 223 escalations (92%) but silently left the 17
  fraud-blocked escalations still dead a partial fix that looked complete
  in casual testing but would have failed a closer check or a different seed.
- **Pass 2**, after specifically checking the fraud-threshold interaction,
  exempted `ESCALATE` from both automation-risk gates. Idempotency still
  applies unconditionally there's never a reason to escalate an
  already-recovered payment.

**How we know it's fixed:** `test_escalation_path_is_reachable_at_batch_scale`
pins the exact count 223 of 223 escalations execute on the real seeded
batch. `test_guard_approves_high_fraud_escalation` and
`test_guard_still_blocks_escalation_only_for_already_recovered` cover the
two boundary cases directly. All verified to fail against the pass-1-only
code and pass against the complete fix.

---

## 5. A misleading calibration comparison, resolved by adding Brier score

**What looked wrong at first:** the model's per-bin Expected Calibration
Error (ECE) measured *worse* than a naive model that always guesses 50%
(`naive_constant_ece`). On the surface, this looked like the model was worse
than blind guessing.

**What was actually happening:** `naive_constant_ece` is `|0.5 - overall
observed rate|` a single, crude number that depends entirely on where the
batch's overall recovery rate happens to land. It can look artificially good
or bad purely from population composition, with no relationship to whether
the model can actually tell easy cases from hard ones. After the `ESCALATE`
fix (#4) added 223 near-zero-recovery cases into the executed population,
the overall observed rate shifted, and `naive_constant_ece` swung from 0.023
to 0.141 a large change caused entirely by which transactions were now
included, not by any change to the model itself.

**The fix:** added Brier score alongside ECE. `naive_constant_brier` is
**mathematically fixed at exactly 0.25** for any batch of binary outcomes
(`(0.5-0)² = (0.5-1)² = 0.25`, always) a fixed, non-gameable baseline
that can't shift with population composition. The real model scores
**0.178**, robustly below that fixed floor.

**How we know it's fixed:** `test_naive_constant_brier_is_a_fixed_baseline`
asserts the naive baseline is exactly `0.25` across three different
seeds/batches. `test_model_brier_score_beats_naive_baseline` asserts the
real model's score is lower. `test_brier_score_matches_manual_computation`
independently hand-computes the formula and checks it matches.

---

## 6. The dashboard's two-file version failed to load

**What was wrong:** the standalone dashboard was initially split into two
files `index.html` (logic) loading `dashboard_data.js` (data) via
`<script src="dashboard_data.js">` specifically to make data regeneration
a one-command operation without touching the logic file. This works when
served from a real web server, but fails when the HTML file is opened
directly (`file://`, or previewed as a single uploaded file): the browser
cannot resolve the sibling file, and the page fails with `Uncaught
ReferenceError: DATA is not defined`.

**The fix:** went back to a single, self-contained HTML file, with the data
embedded inline between `/* RAZREVREC_DATA_START */` and `/* RAZREVREC_DATA_END
*/` marker comments. The one-command regeneration property was preserved by
having `src/export_static_dashboard.py` edit the file **in place** finding
the markers and replacing only the text between them rather than writing
to a second file.

**How we know it's fixed:** the regeneration script was run twice in a row
against a real copy of the file to confirm idempotency (no marker
duplication, no corruption), and the resulting file was executed in a real
DOM engine (not just read) to confirm it renders, the RazRevRec/baseline
toggle works, and the browser-side SHA-256 tamper-evidence check correctly
verifies real recorded hashes from the actual audit trail.

---

## 7. A related design decision, made deliberately rather than by default

Escalating an opted-out customer's case for **internal human review** is
allowed `ESCALATE` was deliberately never added to `CONTACT_ACTIONS`, so
the opt-out check doesn't apply to it. This is intentional: opting out means
"don't message me," not "don't let your fraud/risk team look at my case
internally." Flagging this explicitly here because it's the kind of default
that's easy to leave undecided rather than choose on purpose.