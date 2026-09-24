"""Signal extraction.

The lexicon turns raw text into :class:`~scout.models.Signal` records. It is the
only place in the pipeline that decides *what a piece of text is about*, and it
never produces a claim of its own: a signal is always a literal term that really
occurs in the text, together with the surrounding context so the site can show
the reader exactly where it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..models import Signal
from . import terms as T

__all__ = ["Lexicon", "LEXICON", "split_identifier", "MEASURED_EFFECT_RE"]

# A number followed by a unit, e.g. "12 ms", "30fps", "1.5x", "40%".
MEASURED_EFFECT_RE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*"
    r"(?:%|x\b|ms\b|us\b|µs\b|ns\b|s\b|fps\b|hz\b|khz\b|mhz\b|"
    r"kb\b|mb\b|gb\b|mw\b|mbps\b|frames?\b|cycles?\b)",
    re.IGNORECASE,
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def split_identifier(name: str) -> str:
    """Turn ``SensorSyncManager`` / ``sensor_sync_manager`` into spaced words.

    Multi-word lexicon terms can then match identifiers, not only prose.
    """
    spaced = _CAMEL_BOUNDARY.sub(" ", str(name))
    return re.sub(r"[_\-.:]+", " ", spaced).lower()


def _term_pattern(term: str) -> str:
    stem = term.endswith("*")
    body = term[:-1] if stem else term
    # Escape, then let spaces match separators that survive identifier splitting.
    # ``re.escape`` leaves plain spaces alone on Python 3.7+, so both spellings
    # are replaced to stay correct across versions.
    escaped = (
        re.escape(body.lower())
        .replace("\\ ", "[\\s_\\-\\.]+")
        .replace(" ", "[\\s_\\-\\.]+")
    )
    lead = r"(?<![a-z0-9])" if body[:1].isalnum() else ""
    if stem:
        return lead + escaped + r"\w*"
    trail = "" if body.endswith(("_", "/")) or not body[-1:].isalnum() else r"(?![a-z0-9])"
    return lead + escaped + trail


@dataclass
class _Entry:
    kind: str
    family: str
    term: str
    weight: float


class Lexicon:
    """Compiles the term tables into one scanning regex per signal kind."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, _Entry]] = {}
        self._regex: dict[str, re.Pattern[str]] = {}
        tables = {
            "domain": T.DOMAIN,
            "problem": T.PROBLEM,
            "mechanism": T.MECHANISM,
            "effect": T.EFFECT,
            "evaluation": T.EVALUATION,
            "triviality": T.TRIVIALITY,
        }
        for kind, table in tables.items():
            self._compile(kind, table)
        self._known_patterns = re.compile(
            "|".join(_term_pattern(p) for p in T.KNOWN_PATTERNS), re.IGNORECASE
        )
        self._platform_standard = re.compile(
            "|".join(_term_pattern(p) for p in T.PLATFORM_STANDARD), re.IGNORECASE
        )

    def _compile(self, kind: str, table: dict[str, tuple[float, list[str]]]) -> None:
        entries: dict[str, _Entry] = {}
        alternatives: list[str] = []
        index = 0
        for family, (weight, term_list) in table.items():
            for term in term_list:
                group = f"{kind[:3]}{index}"
                index += 1
                entries[group] = _Entry(kind=kind, family=family, term=term.rstrip("*"),
                                        weight=weight)
                alternatives.append(f"(?P<{group}>{_term_pattern(term)})")
        self._entries[kind] = entries
        self._regex[kind] = re.compile("|".join(alternatives), re.IGNORECASE)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def match(self, text: str, kinds: Iterable[str] | None = None,
              context_width: int = 110, max_per_kind: int = 12) -> list[Signal]:
        """Return de-duplicated signals found in ``text``.

        Only the first occurrence of each term is kept, together with the text
        around it, so that a candidate page can quote the real source line.
        """
        if not text:
            return []
        lowered = text.lower()
        wanted = list(kinds) if kinds else list(self._regex)
        out: list[Signal] = []
        for kind in wanted:
            regex = self._regex.get(kind)
            if regex is None:
                continue
            seen: set[str] = set()
            for m in regex.finditer(lowered):
                group = m.lastgroup
                if group is None:
                    continue
                entry = self._entries[kind][group]
                if entry.term in seen:
                    continue
                seen.add(entry.term)
                start = max(0, m.start() - context_width // 2)
                end = min(len(text), m.end() + context_width // 2)
                context = " ".join(text[start:end].split())
                out.append(
                    Signal(kind=entry.kind, term=entry.term, family=entry.family,
                           weight=entry.weight, context=context)
                )
                if len(seen) >= max_per_kind:
                    break
        return out

    def has_measured_effect(self, text: str) -> str | None:
        """Return the first number-with-unit found, which anchors an effect claim."""
        m = MEASURED_EFFECT_RE.search(text or "")
        return m.group(0).strip() if m else None

    def known_pattern_hits(self, text: str) -> list[str]:
        return sorted({m.group(0).lower() for m in self._known_patterns.finditer(text or "")})

    def platform_standard_hits(self, text: str) -> list[str]:
        return sorted({m.group(0).lower() for m in self._platform_standard.finditer(text or "")})


LEXICON = Lexicon()
