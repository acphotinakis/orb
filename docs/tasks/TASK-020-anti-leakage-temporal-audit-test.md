# TASK-020: Temporal Causality & Anti-Leakage Audit Test Suite

## Objective

Implement a specialized causality audit test suite in `tests/anti_leakage/test_temporal_causality.py` to mathematically prove the absence of lookahead bias, verify opening range immutability, and ensure zero information leakage across time boundaries.

## Scope

### In Scope

- Audit test cases validating non-negotiable quantitative causality rules:
  1. **Opening Range Immutability:** Injecting extreme price spikes into bars timestamped $\ge 09:45:00$ ET must have zero effect on `OR High`, `OR Low`, or `OR Width`.
  2. **Signal Causality Invariance:** Adding, removing, or altering future bars ($t+1, t+2, \dots$) must not change the signal generation decision or calculated stop/target levels at bar $t$.
  3. **Sequential Stream Invariance:** Assert that feeding bars to the strategy one-at-a-time (real-time stream simulation) yields the exact identical signal series and trade results as bulk historical processing.
  4. **No Full-Session Metric Leakage:** Verify that intraday decisions do not reference session-level aggregated metrics (such as full-day high, full-day low, or session close).
  5. **Timestamp Strict Ordering:** Assert that any out-of-order bar injection triggers an immediate `TemporalLeakageError`.

### Out of Scope

- General software unit testing (handled in TASK-019).

## Dependencies

- TASK-001
- TASK-002
- TASK-003
- TASK-008
- TASK-009
- TASK-010
- TASK-013

## Requirements

1. Use automated perturbation testing: mutate future bar slices and assert that past outputs remain strictly bitwise identical.
2. Formally verify that the opening range calculation window is $[09:30:00, 09:45:00)$ and excludes $09:45:00$.
3. All causality audit tests must pass without exception.

## Implementation Details

1. Create `tests/anti_leakage/test_temporal_causality.py`.
2. Implement audit test functions:
   ```python
   def test_opening_range_future_invariance():
       """Proves that prices at 09:45+ cannot alter 09:30-09:44 OR boundaries."""
       ...

   def test_signal_future_invariance():
       """Proves that future price paths do not influence past signal triggers."""
       ...

   def test_streaming_vs_batch_equivalence():
       """Proves that incremental bar streaming equals bulk processing."""
       ...

   def test_out_of_order_bar_rejection():
       """Proves that non-monotonic timestamps raise TemporalLeakageError."""
       ...
   ```

## Interfaces / Contracts

```text
tests/
└── anti_leakage/
    ├── __init__.py
    └── test_temporal_causality.py
```

## Data / File Changes

- Create `tests/anti_leakage/__init__.py`
- Create `tests/anti_leakage/test_temporal_causality.py`

## Validation

1. Run `pytest tests/anti_leakage/ -v`.
2. Introduce an intentional lookahead bug in a mock calculator (e.g. using `session_df['high'].max()`) and confirm that the audit test fails immediately.
3. Revert bug and verify all audit tests pass cleanly.

## Acceptance Criteria

- [ ] Future price perturbations after 09:45 produce zero changes in the calculated opening range.
- [ ] Future bars cannot alter past entry signals or risk parameters.
- [ ] Incremental streaming simulation produces bitwise-identical trades to batch execution.
- [ ] Non-chronological bars raise `TemporalLeakageError`.

## Notes

- Temporal causality is the cornerstone of quantitative credibility; passing this audit guarantees that backtested performance is free from lookahead contamination.
