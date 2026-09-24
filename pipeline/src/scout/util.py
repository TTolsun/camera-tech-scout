"""Small shared helpers: logging, identifiers, glob matching, timing."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

_LOG_FORMAT = "%(levelname).3s %(message)s"


def setup_logging(verbose: bool = False) -> None:
    # Korean output has to survive a Windows console whose code page is not
    # UTF-8, which is the default on the machines this runs on.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=_LOG_FORMAT,
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


log = logging.getLogger("scout")


@contextmanager
def step(title: str) -> Iterator[None]:
    """Log the start and the wall-clock duration of a pipeline stage."""
    log.info("- %s", title)
    started = time.perf_counter()
    try:
        yield
    finally:
        log.info("  %s finished in %s", title, human_duration(time.perf_counter() - started))


def human_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{seconds % 60:.0f}s"


def stable_id(prefix: str, *parts: Any) -> str:
    """Deterministic identifier so that ids survive across scans."""
    digest = hashlib.sha1("\0".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:12]}"


def slugify(text: str, limit: int = 72) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:limit] or "item"


def match_any(path: str, patterns: Iterable[str]) -> bool:
    """Glob match with ``**`` support, evaluated against a POSIX relative path."""
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        # `src/**` should also match the directory entry `src/a/b.c`.
        if pattern.endswith("/**") and path.startswith(pattern[:-2]):
            return True
        # A bare `*.md` pattern should match at any depth.
        if pattern.startswith("*.") and fnmatch.fnmatch(os.path.basename(path), pattern):
            return True
    return False


def read_text(path: Path, max_bytes: int) -> str | None:
    """Read a text file, skipping anything binary or larger than ``max_bytes``."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def dedupe_keep_order(items: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def truncate(text: str, limit: int = 320) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


# Deep clones under a long checkout path fail on Windows, because a cloned file
# can exceed MAX_PATH even when the directory we create is short.
_WINDOWS_PATH_BUDGET = 150


def warn_if_path_too_long(path: Path, what: str) -> bool:
    """Warn when a directory is too deep for Windows to clone into.

    Returns ``True`` when the path looks unusable, so the caller can suggest a
    shorter one rather than failing with a confusing ``WinError 206``.
    """
    if os.name != "nt":
        return False
    length = len(str(path.resolve()))
    if length < _WINDOWS_PATH_BUDGET:
        return False
    log.warning(
        "%s \uacbd\ub85c\uac00 %d\uc790\uc785\ub2c8\ub2e4. Windows\uc758 MAX_PATH \uc81c\ud55c \ub54c\ubb38\uc5d0 clone\uc774 \uc2e4\ud328\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4. "
        "\ub354 \uc9e7\uc740 \uacbd\ub85c\ub97c --cache \ub85c \uc9c0\uc815\ud558\uc2ed\uc2dc\uc624. \uc608: --cache C:/scout-cache",
        what, length,
    )
    return True


# Comment markers across the languages the scanner reads. Used to tell a deleted
# line of code from a deleted line of documentation, which is the difference
# between "this approach was removed" and "this sentence was edited".
_COMMENT_PREFIXES = ("//", "/*", "*", "*/", "#", "<!--", "-->", "--", ";", '"""', "'''")


def looks_like_comment(line: str) -> bool:
    return line.lstrip().startswith(_COMMENT_PREFIXES)
