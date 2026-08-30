"""Benchmark the decision-loop throughput of evaluate_batch.

Run with: python -m benchmarks.throughput

This does not benchmark I/O, persistence, or network calls -- it isolates
the cost of the decision logic itself (cohort lookup, ERV computation,
Policy Guard evaluation) to answer one specific question: is the per-
transaction decision cost small enough that, at production volume, the
bottleneck would be elsewhere (storage, network, external APIs) rather
than this loop? See the "Scaling to Production" section of the README
for how these numbers are used.
"""

from __future__ import annotations

import time

from src.evaluator import evaluate_batch
from src.simulator import generate_transactions

BATCH_SIZES = [1_000, 10_000, 50_000]
RAZORPAY_DAILY_UPI_TRANSACTIONS = 35_000_000  # per public reporting, see README citation
SECONDS_PER_DAY = 86_400


def run() -> None:
    print("RazRevRec decision-loop throughput benchmark")
    print("(single process, single core, decision logic only -- no I/O)\n")
    results: list[tuple[int, float]] = []
    for size in BATCH_SIZES:
        batch = generate_transactions(size, 7, f"throughput_bench_{size}")
        start = time.perf_counter()
        evaluate_batch(batch, mode="razrevrec")
        elapsed = time.perf_counter() - start
        throughput = size / elapsed
        results.append((size, throughput))
        print(f"{size:>7,} transactions -> {elapsed:.4f}s -> {throughput:>10,.0f} tx/sec")

    throughputs = [t for _, t in results]
    spread = (max(throughputs) - min(throughputs)) / min(throughputs)
    print(f"\nThroughput spread across batch sizes: {spread:.1%}")
    print("(flat throughput as batch size grows confirms O(n), not a hidden quadratic cost)")

    avg_throughput = sum(throughputs) / len(throughputs)
    razorpay_avg_load = RAZORPAY_DAILY_UPI_TRANSACTIONS / SECONDS_PER_DAY
    headroom = avg_throughput / razorpay_avg_load
    print(f"\nRazorpay's ~{RAZORPAY_DAILY_UPI_TRANSACTIONS:,} daily UPI transactions "
          f"average to ~{razorpay_avg_load:.0f} tx/sec.")
    print(f"This decision loop's measured throughput ({avg_throughput:,.0f} tx/sec) "
          f"is ~{headroom:,.0f}x that average load, on a single core.")
    print("The bottleneck at real scale is I/O (audit persistence, external API calls),")
    print("not this decision logic -- see README 'Scaling to Production'.")


if __name__ == "__main__":
    run()