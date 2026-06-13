"""Plan 13 intent handle-type slice gate."""

import httpx
import pytest

from codeoracle.agent.nodes.intent import EXPLAIN_SYMBOL_HANDLE_TYPES
from codeoracle.models.evidence import HandleType, VerdictStatus
from codeoracle.models.query import QueryResult
from tests.integration.slice._helpers import (
    drive_reindex_to_applied,
    extract_handle_types,
)

_SYMBOL = "codeoracle.services.verifier.service.VerifierService.verify"
_ALLOWED = {
    HandleType.CODE_SPAN,
    HandleType.DOC_PARAGRAPH,
    HandleType.GRAPH_QUERY,
}
_FORBIDDEN = {HandleType.TRACE_SPAN, HandleType.DB_RECORD}


@pytest.mark.slice
async def test_intent_classifier_never_produces_unsupported_handle_types(
    api_client: httpx.AsyncClient,
    clean_snapshot: None,
) -> None:
    snapshot = await drive_reindex_to_applied(api_client, base_sha=None)
    response = await api_client.post(
        "/v1/query",
        json={
            "type": "explain_symbol",
            "symbol": _SYMBOL,
            "corpus": "self",
            "snapshot_sha": snapshot.sha,
        },
    )
    assert response.status_code == 200, response.text
    result = QueryResult.model_validate(response.json())
    assert result.verdict is not None
    assert result.verdict.status == VerdictStatus.PROVEN

    emitted = {HandleType(value) for value in extract_handle_types(result)}
    assert emitted <= _ALLOWED
    assert emitted.isdisjoint(_FORBIDDEN)
    assert set(EXPLAIN_SYMBOL_HANDLE_TYPES) == _ALLOWED
