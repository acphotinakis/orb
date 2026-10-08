"""
src.strategy — ORB strategy signal generation engine.

Provides:
- OpeningRangeCalculator: computes and freezes 09:30–09:44 ET range bands (TASK-009)
- OpeningRange: immutable dataclass holding OR High, OR Low, OR Width per session
  (TASK-009)
- SignalGenerator: detects bar-close breakouts and emits typed Signal objects (TASK-010)
- Signal: immutable dataclass capturing entry, stop loss, and 2R target levels
  (TASK-010)
"""
