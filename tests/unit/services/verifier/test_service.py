"""Unit tests for VerifierService — the PROVEN / UNKNOWN / REFUTED decision."""

import asyncio
import logging

import pytest
import structlog
from structlog.testing import capture_logs

from codeoracle.core.exceptions import (
    ResolverError,
    UnsupportedHandleTypeError,
)
from codeoracle.core.logging import get_logger
from codeoracle.models.evidence import (
    Citation,
    Claim,
    EvidenceHandle,
    HandleType,
    VerdictStatus,
)
from codeoracle.services.handle_resolution.strategies.graph_query import (
    stringify_cypher_rows,
)
from codeoracle.services.verifier.service import (
    PROVEN_CONFIDENCE_FLOOR,
    VerifierService,
)


@pytest.fixture(autouse=True)
def _prod_representative_structlog():
    """
    Pin a *prod-representative* filtering wrapper for each test, then restore.

    `structlog.testing.capture_logs()` swaps the processor chain but leaves
    the wrapper class untouched. A filtering wrapper therefore decides — at the
    bound-logger method, *before* the swapped capture processor runs — whether
    an event is emitted at all. So the wrapper this fixture installs is exactly
    what determines which levels of a plain `get_logger` are observable in
    tests
    — and, by contrast, makes the `_AUDIT` channel's fixed-INFO floor visible.

    Pinning the global wrapper to
    `make_filtering_bound_logger(logging.WARNING)`
    makes the test environment match a real operator who has raised prod
    verbosity to WARNING — the representative setting that exposes audit D-9.3.
    Under it, a `log_level`-filtered logger's `info`/`warning` lines are
    dropped
    (see `test_plain_logger_info_is_filtered_at_prod_level`, the anti-mask
    control), while `VerifierService`'s `_AUDIT` channel — built with a
    fixed-INFO floor independent of `settings.log_level` — still emits all
    three
    citation-resolution outcomes. A non-filtering `BoundLogger` here would mask
    D-9.3: every level would pass, so the prod-filtered logger's dropped lines
    would look emitted and criterion-11's prod gap would be invisible.

    structlog configuration is process-global mutable state. The fixture
    snapshots it on entry and restores that exact snapshot on exit, rather
    than calling `reset_defaults()` on teardown — a bare reset would leave
    structlog at library defaults and silently discard whatever configuration
    another test module had installed. Snapshot-and-restore leaves global
    state exactly as the test found it, in both directions."""
    saved = structlog.get_config()
    structlog.reset_defaults()
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    yield
    structlog.configure(**saved)


# --- local builders ---------------------------------------------------------


def _handle(handle_type=HandleType.CODE_SPAN, locator="file://app/x.py:1-2"):
    return EvidenceHandle(
        type=handle_type, locator=locator, snapshot_sha="abcd1234"
    )


def _citation(
    excerpt="x = 1",
    *,
    handle_type=HandleType.CODE_SPAN,
    locator="file://app/x.py:1-2",
    source_trust=10,
):
    return Citation(
        handle=_handle(handle_type, locator),
        excerpt=excerpt,
        source_trust=source_trust,
    )


def _claim(*, citations=None, confidence=0.9, contradictions=None):
    return Claim(
        statement="the function returns 1",
        citations=[_citation()] if citations is None else citations,
        confidence=confidence,
        contradictions=[] if contradictions is None else contradictions,
    )


class _FakeResolver:
    """Test double for HandleResolverService. Duck-types `.resolve()` — tests
    live outside `ty`'s configured `src`, so structural compatibility suffices.

    `by_locator` maps each handle locator to either the string to return or an
    exception instance to raise."""

    def __init__(self, by_locator: dict[str, str | Exception]) -> None:
        self._by_locator = by_locator
        self.calls: list[str] = []

    async def resolve(self, handle: EvidenceHandle) -> str:
        await asyncio.sleep(0)  # behave like a real coroutine; yields the loop
        self.calls.append(handle.locator)
        outcome = self._by_locator[handle.locator]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- the confidence floor ---------------------------------------------------


@pytest.mark.asyncio
async def test_verify_below_floor_returns_unknown_without_resolving() -> None:
    """Confidence below the floor short-circuits to UNKNOWN — no citation is
    even resolved, and resolved_citations is empty."""
    resolver = _FakeResolver({})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(confidence=0.3)
    )
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_confidence_at_floor_is_not_abstained() -> None:
    """Confidence exactly at the floor is NOT below it — the check is `<`, not
    `<=` — so verify proceeds to resolution."""
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(confidence=PROVEN_CONFIDENCE_FLOOR)
    )
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_confidence_just_below_floor_is_unknown() -> None:
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(confidence=PROVEN_CONFIDENCE_FLOOR - 0.01)
    )
    assert verdict.status == VerdictStatus.UNKNOWN


# --- the evidence-free-citation guard ---------------------------------------


@pytest.mark.asyncio
async def test_verify_empty_excerpt_citation_returns_unknown() -> None:
    """A citation whose excerpt is the empty string carries no checkable
    evidence. verify() must return UNKNOWN *before resolving anything* — a
    byte-comparison against `""` would otherwise PROVEN-match any handle that
    resolves to empty content (plan Invariant 12). The resolver is seeded so
    the handle *would* resolve to matching empty content, then asserted never
    to be called — proving the guard fires pre-resolution, not post-match."""
    resolver = _FakeResolver({"file://app/x.py:1-2": ""})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="")])
    )
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_whitespace_only_excerpt_returns_unknown() -> None:
    """A whitespace-only excerpt normalizes to `""`, so it is evidence-free by
    the same reasoning as a literally empty one. The guard runs the excerpt
    through `_normalize_for_comparison`, so it catches both (plan Invariant
    12).
    """
    resolver = _FakeResolver({"file://app/x.py:1-2": "   \n\t  "})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="   \n\t  ")])
    )
    assert verdict.status == VerdictStatus.UNKNOWN
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_verify_empty_excerpt_with_valid_citations_unknown() -> None:
    """One evidence-free citation among otherwise-valid ones forces UNKNOWN for
    the whole claim, and short-circuits before *any* citation is resolved — so
    the valid sibling is never fetched either. The guard is claim-level."""
    resolver = _FakeResolver({"file://a.py:1-1": "a", "file://b.py:1-1": ""})
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="", locator="file://b.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations == []
    assert resolver.calls == []


# --- the three-way verdict --------------------------------------------------


@pytest.mark.asyncio
async def test_verify_all_citations_resolve_and_match_returns_proven() -> None:
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.PROVEN
    assert len(verdict.resolved_citations) == 1
    assert verdict.resolved_citations[0].resolved is True
    assert verdict.resolved_citations[0].matches_excerpt is True


@pytest.mark.asyncio
async def test_verify_unresolved_citation_returns_unknown() -> None:
    """A citation that raises ResolverError makes the verdict UNKNOWN — the
    platform cannot prove a claim it cannot fetch evidence for."""
    resolver = _FakeResolver(
        {"file://app/x.py:1-2": ResolverError("sourcebot 404")}
    )
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.UNKNOWN
    assert "failed to resolve" in verdict.reason
    assert verdict.resolved_citations[0].resolved is False


@pytest.mark.asyncio
async def test_verify_content_drift_returns_refuted() -> None:
    """A citation that resolves but whose live content differs from the cited
    excerpt makes the verdict REFUTED."""
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 999  # tampered"})
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.REFUTED
    assert verdict.resolved_citations[0].matches_excerpt is False


@pytest.mark.asyncio
async def test_verify_unresolved_dominates_mismatch() -> None:
    """When one citation is unresolved AND another has drifted content, the
    verdict is UNKNOWN, not REFUTED (plan Invariant 4). REFUTED is the strong
    'disproven' claim; a missing citation means the platform lacks the
    evidence to make it."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": ResolverError("a is gone"),  # unresolved
            "file://b.py:1-1": "COMPLETELY DIFFERENT",  # resolved but drifted
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.UNKNOWN


# --- resolution behavior ----------------------------------------------------


@pytest.mark.asyncio
async def test_verify_cosmetic_drift_is_tolerated() -> None:
    """Content that differs only by trailing whitespace and line endings still
    matches — normalization absorbs cosmetic drift (plan Invariant 6)."""
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1   \r\n"})
    verdict = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(excerpt="x = 1")])
    )
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_resolves_every_citation() -> None:
    """Every citation on the claim is resolved — not just the first."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": "a",
            "file://b.py:1-1": "b",
            "file://c.py:1-1": "c",
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
            _citation(excerpt="c", locator="file://c.py:1-1"),
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN
    assert sorted(resolver.calls) == [
        "file://a.py:1-1",
        "file://b.py:1-1",
        "file://c.py:1-1",
    ]


@pytest.mark.asyncio
async def test_verify_does_not_resolve_contradictions() -> None:
    """contradictions are scored evidence for downstream display — verify()
    resolves `citations` only, never `contradictions`."""
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    claim = _claim(
        citations=[_citation(excerpt="x = 1", locator="file://app/x.py:1-2")],
        contradictions=[
            _citation(excerpt="never fetched", locator="file://other.py:9-9")
        ],
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN
    assert resolver.calls == [
        "file://app/x.py:1-2"
    ]  # the contradiction was NOT resolved


@pytest.mark.asyncio
async def test_verify_verdict_independent_of_source_trust() -> None:
    """source_trust never gates the verdict (plan Invariant 3): two claims that
    differ ONLY in source_trust get the same status. The value still flows
    through, untouched, into the resolved citations."""
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    verdict_high = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(source_trust=10)])
    )
    verdict_low = await VerifierService(resolver=resolver).verify(
        _claim(citations=[_citation(source_trust=0)])
    )
    assert verdict_high.status == verdict_low.status == VerdictStatus.PROVEN
    assert verdict_high.resolved_citations[0].citation.source_trust == 10
    assert verdict_low.resolved_citations[0].citation.source_trust == 0


# --- exception handling -----------------------------------------------------


@pytest.mark.asyncio
async def test_verify_propagates_non_resolver_error() -> None:
    """Only ResolverError is an expected failure. Any other exception is a bug
    and must propagate uncaught (plan Invariant 5) — it is NOT silently turned
    into an unresolved citation."""
    resolver = _FakeResolver(
        {"file://app/x.py:1-2": RuntimeError("strategy bug")}
    )
    with pytest.raises(RuntimeError, match="strategy bug"):
        await VerifierService(resolver=resolver).verify(_claim())


@pytest.mark.asyncio
async def test_verify_unsupported_handle_type_is_treated_as_unresolved() -> (
    None
):
    """UnsupportedHandleTypeError is a ResolverError subclass, so it is caught
    by the same `except` and produces UNKNOWN — not an uncaught crash."""
    resolver = _FakeResolver(
        {"file://app/x.py:1-2": UnsupportedHandleTypeError("trace_span")}
    )
    verdict = await VerifierService(resolver=resolver).verify(_claim())
    assert verdict.status == VerdictStatus.UNKNOWN
    assert verdict.resolved_citations[0].resolved is False


# --- per-citation structured logging (spec §8.5 criterion 11) ---------------
#
# These tests run under the autouse `_prod_representative_structlog` fixture,
# which pins a WARNING-floor global wrapper. That makes them a real prod-level
# check: an `info`/`warning` line emitted through the global wrapper is
# dropped,
# so the only reason the assertions below see all three outcomes is that
# `VerifierService` logs them on the fixed-INFO `_AUDIT` channel. The control
# test directly below pins down the anti-mask: it proves the fixture filters a
# plain logger, so if Plan 9 ever regressed `_AUDIT` back to a `log_level`-
# filtered logger, every `info`/`warning` assertion here would start failing.


@pytest.mark.asyncio
async def test_plain_logger_info_is_filtered_at_prod_level() -> None:
    """Anti-mask control for audit D-9.3.

    Under the prod-representative WARNING-floor fixture, a plain `get_logger`
    `info` (the channel Plan 9 deliberately does NOT use for the audit line) is
    dropped, while the `VerifierService` `_AUDIT` channel's `info` survives in
    the same capture. This is what guarantees the four outcome tests below are
    testing the prod path, not a non-filtering test stand-in: if the audit line
    were emitted on a `log_level`-filtered logger, this capture would be empty.
    """
    audit = structlog.wrap_logger(
        None, wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
    )
    with capture_logs() as logs:
        get_logger("plain").info("verifier.plain_info_should_be_dropped")
        audit.info("verifier.audit_info_should_survive")
    events = [entry["event"] for entry in logs]
    assert "verifier.plain_info_should_be_dropped" not in events
    assert events == ["verifier.audit_info_should_survive"]


@pytest.mark.asyncio
async def test_verify_logs_info_on_resolved_match() -> None:
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 1"})
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_resolved"
    assert logs[0]["log_level"] == "info"
    assert logs[0]["handle_type"] == "code_span"
    assert logs[0]["locator"] == "file://app/x.py:1-2"


@pytest.mark.asyncio
async def test_verify_logs_warning_on_unresolved() -> None:
    resolver = _FakeResolver(
        {"file://app/x.py:1-2": ResolverError("sourcebot 404")}
    )
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_unresolved"
    assert logs[0]["log_level"] == "warning"
    assert "sourcebot 404" in logs[0]["error"]


@pytest.mark.asyncio
async def test_verify_logs_error_on_excerpt_mismatch() -> None:
    resolver = _FakeResolver({"file://app/x.py:1-2": "x = 999"})
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(_claim())
    assert len(logs) == 1
    assert logs[0]["event"] == "verifier.citation_excerpt_mismatch"
    assert logs[0]["log_level"] == "error"


@pytest.mark.asyncio
async def test_verify_emits_one_log_line_per_citation() -> None:
    """Spec §8.5 criterion 11: exactly one structured log line per citation
    resolution outcome — here, three citations with three distinct outcomes."""
    resolver = _FakeResolver(
        {
            "file://a.py:1-1": "a",  # -> info
            "file://b.py:1-1": "DRIFTED",  # -> error
            "file://c.py:1-1": ResolverError("gone"),  # -> warning
        }
    )
    claim = _claim(
        citations=[
            _citation(excerpt="a", locator="file://a.py:1-1"),
            _citation(excerpt="b", locator="file://b.py:1-1"),
            _citation(excerpt="c", locator="file://c.py:1-1"),
        ]
    )
    with capture_logs() as logs:
        await VerifierService(resolver=resolver).verify(claim)
    assert len(logs) == 3
    assert {log["log_level"] for log in logs} == {"info", "error", "warning"}


# --- the GRAPH_QUERY excerpt contract (Plan 8) ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [
            {"result": '"alpha"'},
            {"result": '"beta"'},
        ],  # two agtype-quoted strings
        [{"result": 3}],  # one int
        [
            {"result": 3.5},
            {"result": True},
            {"result": False},
            {"result": None},
        ],
        [],  # empty result set
    ],
)
async def test_verify_graph_query_excerpt_round_trips_to_proven(
    rows: list[dict[str, object]],
) -> None:
    """A GRAPH_QUERY citation whose excerpt is exactly what GraphQueryResolver
    emits verifies as PROVEN against that same output.

    The expected excerpt is **not hand-written** here — it is produced by
    Plan 8's real `stringify_cypher_rows` helper (imported at the top of this
    file) on both sides of the comparison. If Plan 8 ever changes
    `separators=(", ", ": ")`, its `sort_keys` default, or its agtype-unwrap
    rule, this test breaks immediately at the Plan-9 gate *because* the
    helper output changed — D-9.2 fix: no Plan-9-side assumption of the
    Plan 8 contract is encoded here, the helper *is* the contract."""
    expected_excerpt = stringify_cypher_rows(rows)
    locator = "cypher://q/42"
    resolver = _FakeResolver({locator: expected_excerpt})
    claim = _claim(
        citations=[
            _citation(
                excerpt=expected_excerpt,
                handle_type=HandleType.GRAPH_QUERY,
                locator=locator,
                source_trust=9,
            )
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.PROVEN


@pytest.mark.asyncio
async def test_verify_graph_query_spacing_drift_refutes() -> None:
    """An LLM that emits compact JSON (`["alpha","beta"]`) does NOT match the
    real `stringify_cypher_rows` output (which uses `separators=(", ", ": ")`)
    and is correctly REFUTED. Normalization does not paper over a structural
    formatting difference.

    The *contract* side calls Plan 8's helper directly (D-9.2 fix), so if
    Plan 8 ever switched to compact separators the helper output would equal
    `compact_excerpt`, this test's `!=` assertion would fail, and Plan 9's
    suite would surface the breaking change before the runtime suite ever
    saw a spurious PROVEN verdict."""
    rows: list[dict[str, object]] = [
        {"result": '"alpha"'},
        {"result": '"beta"'},
    ]
    contract_excerpt = stringify_cypher_rows(rows)  # Plan 8's real output
    compact_excerpt = '["alpha","beta"]'  # what a naive LLM emits
    assert contract_excerpt != compact_excerpt, (
        "Plan 8's stringify_cypher_rows output collides with compact JSON — "
        "the separator contract changed; this test no longer pins drift."
    )

    locator = "cypher://q/7"
    resolver = _FakeResolver({locator: contract_excerpt})
    claim = _claim(
        citations=[
            _citation(
                excerpt=compact_excerpt,
                handle_type=HandleType.GRAPH_QUERY,
                locator=locator,
                source_trust=9,
            )
        ]
    )
    verdict = await VerifierService(resolver=resolver).verify(claim)
    assert verdict.status == VerdictStatus.REFUTED
