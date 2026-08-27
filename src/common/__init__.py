"""
src.common — Shared infrastructure utilities.

Provides:
- AppConfig and sub-config dataclasses (TASK-002)
- Timezone and session boundary helpers (TASK-003)
- Centralized logging factory (TASK-004)
- Domain-specific exception hierarchy (TASK-004)
- PathManager for centralized path resolution (SFIX-001)
"""

from src.common.paths import PathManager

__all__ = ["PathManager"]
