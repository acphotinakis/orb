"""Read-only historical code captured with an execution, never worktree fallback."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Mapping

from src.backtest.trace import TRACE_SOURCE_ALLOWLIST, RULE_SOURCE_MAP
from src.services.artifact_store import ensure_within_root, source_code_fingerprint

SNAPSHOT_VERSION = 1


def capture_source_snapshot(repo_root: Path | None = None) -> dict:
    root = repo_root or Path(__file__).resolve().parents[2]
    files = {}
    for module in TRACE_SOURCE_ALLOWLIST:
        try:
            path = ensure_within_root(root, root / module)
            text = path.read_text(encoding="utf-8")
            files[module] = {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        except (OSError, ValueError) as exc:
            files[module] = {"unavailable": str(exc)}
    return {"snapshot_version": SNAPSHOT_VERSION, "source": source_code_fingerprint(root),
            "rule_sources": RULE_SOURCE_MAP, "files": files}


def historical_source(snapshot: Mapping, module: str, function: str | None = None) -> str:
    if module not in TRACE_SOURCE_ALLOWLIST:
        raise ValueError("Source module is outside the allowlist")
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise ValueError("Historical source snapshot unavailable or unsupported")
    file = snapshot.get("files", {}).get(module, {})
    text = file.get("text")
    if not isinstance(text, str):
        raise ValueError("Historical source unavailable; no worktree fallback")
    if hashlib.sha256(text.encode()).hexdigest() != file.get("sha256"):
        raise ValueError("Historical source checksum mismatch")
    if function:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
                return "\n".join(text.splitlines()[node.lineno - 1:node.end_lineno])
        raise ValueError("Function unavailable in recorded source")
    return text
