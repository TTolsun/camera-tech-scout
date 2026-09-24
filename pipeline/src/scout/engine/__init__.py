"""Analysis engines.

The deterministic rules engine is always in charge. The optional LLM layer may
only refine prose and add objections on top of what the rules engine already
anchored to evidence, and it is skipped entirely when disabled, unreachable or
running under ``--dry-run``.
"""

from __future__ import annotations

from .llm import LLMClient, LLMReport, LLMSettings

__all__ = ["LLMClient", "LLMReport", "LLMSettings"]
