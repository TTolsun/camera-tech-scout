"""Signal extraction.

The lexicon decides what every later stage sees, and it works by string
matching, so a regression here is invisible in the output until someone reads a
candidate carefully. These tests pin the three properties that were actually got
wrong during development: stem handling, word boundaries, and the separators
allowed inside a multi-word term.
"""

from __future__ import annotations

import pytest

from scout.lexicon import LEXICON, split_identifier


def terms_of(text: str, kind: str) -> set[str]:
    return {s.term for s in LEXICON.match(text) if s.kind == kind}


def kinds_of(text: str) -> set[str]:
    return {s.kind for s in LEXICON.match(text)}


# ---------------------------------------------------------------------------
# Identifier splitting
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("SensorSyncManager", "sensor sync manager"),
        ("sensor_sync_manager", "sensor sync manager"),
        ("start_of_frame", "start of frame"),
        ("IspBufferPool", "isp buffer pool"),
        ("HTTPServer", "http server"),
        ("predictNextSof", "predict next sof"),
    ],
)
def test_split_identifier(identifier, expected):
    assert split_identifier(identifier) == expected


# ---------------------------------------------------------------------------
# Stems
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["prediction", "predictive control", "the code predicted the offset", "predicts"],
)
def test_stem_matches_inflections(text):
    assert "predict" in terms_of(text, "mechanism")


def test_reduction_stem_matches_reducing():
    """`reduce*` missed `reducing`, because the stem is `reduc`."""
    assert "reduc" in terms_of("reducing the jitter", "effect")
    assert "reduc" in terms_of("this reduces latency", "effect")


# ---------------------------------------------------------------------------
# Word boundaries
# ---------------------------------------------------------------------------
def test_short_term_does_not_match_inside_a_word():
    """`sof` must not fire on `software`."""
    assert "sof" not in terms_of("this software is fine", "domain")
    assert "sof" in terms_of("waiting for the sof interrupt", "domain")


def test_term_matches_next_to_a_trailing_underscore():
    """C++ members end in `_`, and the term still has to match."""
    assert "estimator" in terms_of("auto value = estimator_.predict(x);", "mechanism")


def test_term_matches_after_a_leading_underscore():
    assert "predict" in terms_of("void _predictNextFrame();", "mechanism")


def test_numeric_term_boundary():
    assert "3a" in terms_of("the 3a algorithms converge", "domain")
    assert "3a" not in terms_of("value v3ab is unrelated", "domain")


# ---------------------------------------------------------------------------
# Multi-word terms
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    ["start of frame", "start_of_frame", "start-of-frame", "start  of  frame"],
)
def test_multi_word_term_tolerates_separators(text):
    assert "start of frame" in terms_of(text, "domain")


# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------
def test_a_realistic_snippet_produces_every_kind_we_rely_on():
    text = (
        "// Adaptive multi-camera synchronisation.\n"
        "// Previously a fixed vblank caused timestamp drift and dropped frames.\n"
        "// SyncManager now predicts the next start_of_frame and applies drift\n"
        "// compensation, reducing jitter from 3.2 ms to 0.4 ms.\n"
        "// Benchmarked against the fixed vblank baseline.\n"
    )
    kinds = kinds_of(text)

    assert {"domain", "problem", "mechanism", "effect", "evaluation"} <= kinds


def test_triviality_markers_are_detected():
    assert terms_of("run clang-format over the tree, whitespace only", "triviality")
    assert terms_of("bump version and update the changelog", "triviality")


def test_signals_carry_their_context():
    signals = [s for s in LEXICON.match("applies drift compensation here")
               if s.term == "drift compensat"]

    assert signals, "the drift compensation term should match"
    assert "drift compensation" in signals[0].context


def test_match_deduplicates_a_term_within_its_kind():
    """Repeating a term must not multiply its signal.

    A term that appears in two tables still yields one signal per kind. `jitter`
    is deliberately both a `performance` topic and a `timing` problem, so the
    expected result here is one of each, not one in total.
    """
    text = "jitter jitter jitter in the isp pipeline"
    signals = [s for s in LEXICON.match(text) if s.term == "jitter"]

    assert len(signals) == 2
    assert {s.kind for s in signals} == {"domain", "problem"}
    assert len({s.kind for s in signals}) == len(signals), "one signal per kind"


def test_empty_text_produces_no_signals():
    assert LEXICON.match("") == []


# ---------------------------------------------------------------------------
# Measured effects
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("reduced to 3.2 ms", "3.2 ms"),
        ("runs at 30fps", "30fps"),
        ("improved by 40%", "40%"),
        ("1.5x faster", "1.5x"),
        ("uses 120 mW", "120 mW"),
    ],
)
def test_measured_effect_is_found(text, expected):
    assert LEXICON.has_measured_effect(text) == expected


@pytest.mark.parametrize("text", ["v4l2 subdev", "no numbers here", "IPU6"])
def test_measured_effect_is_not_invented(text):
    assert LEXICON.has_measured_effect(text) is None


# ---------------------------------------------------------------------------
# Critic support tables
# ---------------------------------------------------------------------------
def test_known_pattern_hits():
    hits = LEXICON.known_pattern_hits("a singleton holding a shared_ptr")

    assert "singleton" in hits
    assert "shared_ptr" in hits


def test_platform_standard_hits():
    hits = LEXICON.platform_standard_hits("calls VIDIOC_QBUF through v4l2_ioctl")

    assert "vidioc_" in hits
    assert "v4l2_ioctl" in hits


def test_platform_standard_does_not_fire_on_ordinary_text():
    assert LEXICON.platform_standard_hits("an adaptive controller") == []
