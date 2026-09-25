"""Code scanning sees mechanism vocabulary inside the bodies of functions.

C++ camera code rarely names a mechanism in the function that uses it. `run()`
calls `applyDriftCompensation()`, and the lexicon's word boundary cannot see
`Interpolated` inside `getInterpolated`. The scan therefore matches the body in
its identifier-split form as well, and quotes the raw line it came from.
"""

from __future__ import annotations

from scout.evidence import _code_haystack, _quote_source
from scout.lexicon import LEXICON
from scout.symbols import Symbol

BODY = "void Foo::run() { applyDriftCompensation(x); currentCcm_ = getInterpolated(ct); }"


def _symbol(text: str, name: str = "Foo::run", doc: str = "") -> Symbol:
    return Symbol(name=name, kind="function", line_start=1, line_end=1, text=text, doc=doc)


def _mechanisms(text: str) -> set[str]:
    return {s.term for s in LEXICON.match(text, kinds=("mechanism",))}


def test_raw_body_alone_misses_camel_case_calls():
    assert not {"drift compensat", "interpolat"} & _mechanisms(BODY)


def test_code_haystack_sees_mechanisms_in_the_body():
    assert {"drift compensat", "interpolat"} <= _mechanisms(_code_haystack(_symbol(BODY)))


def test_split_only_matches_quote_the_source_line():
    text = "void Foo::run() {\n  applyDriftCompensation(x);\n}"
    symbol = _symbol(text)
    signals = LEXICON.match(_code_haystack(symbol), kinds=("mechanism",))

    _quote_source(signals, symbol.searchable)

    drift = next(s for s in signals if s.term == "drift compensat")
    assert drift.context == "applyDriftCompensation(x);"


def test_raw_matches_keep_their_context():
    # The term sits far from both ends, so its context window stays inside the
    # raw body and never reaches the split name or the split copy.
    padding = "// " + "x" * 80
    text = f"{padding}\n// drift compensation keeps sensors aligned\n{padding}"
    symbol = _symbol(text)
    signals = LEXICON.match(_code_haystack(symbol), kinds=("mechanism",))
    before = {s.term: s.context for s in signals}

    _quote_source(signals, symbol.searchable)

    assert {s.term: s.context for s in signals} == before


def test_short_terms_are_not_quoted_from_a_longer_word():
    text = "// uses a software timer\nvoid f() {\n  predictedSofNs_ = next;\n}"
    symbol = _symbol(text, name="f")
    signals = LEXICON.match(_code_haystack(symbol))

    _quote_source(signals, symbol.searchable)

    sof = next(s for s in signals if s.term == "sof")
    assert sof.context == "predictedSofNs_ = next;"
