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

The comparison is against a retry-only baseline subject to the same non-negotiable safety guard. Customer contacts are reported as an independent customer-impact metric; a retry-only baseline has no contacts, so it would be misleading to claim that RazRevRec sends fewer than that baseline.
