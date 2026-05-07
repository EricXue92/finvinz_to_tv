import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from report import analyst


@pytest.fixture
def fake_data():
    return {
        "ticker": "AAPL",
        "group": "EarningsGap",
        "exchange": "NASDAQ",
        "company_name": "Apple Inc.",
        "market_cap": 3_000_000_000_000,
        "last_price": 200.0,
        "prev_close": 198.0,
        "gap_pct": 1.01,
        "pe_ratio": 30.0,
        "roe": 150.0,
        "institutional_holdings_pct": 60.0,
        "eps_latest_q": 1.1,
        "eps_latest_q_yoy_pct": 10.0,
        "revenue_latest_q": 1.1e9,
        "revenue_latest_q_yoy_pct": 10.0,
        "annual_eps_yoy_3y": [16.67, 14.29, 10.0],
        "annual_revenue_yoy_3y": [16.67, 14.29, 10.0],
        "latest_earnings_date": "2026-03-31",
        "rs_percentile": 95,
    }


def test_build_user_message_includes_ticker_and_data(fake_data):
    msg = analyst.build_user_message(fake_data)
    assert "AAPL" in msg
    assert "EarningsGap" in msg
    assert "Apple Inc." in msg
    # YoY arrays serialized
    assert "16.67" in msg or "16.7" in msg


def _make_anthropic_response(text: str):
    """Mock the SDK response shape: response.content[*].text."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.stop_reason = "end_turn"
    return response


async def test_analyze_ticker_success(fake_data):
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(
        return_value=_make_anthropic_response("## AAPL\n\n**Snapshot**...")
    )
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<system prompt>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "## AAPL" in section
    fake_client.messages.create.assert_awaited_once()


async def test_analyze_ticker_retries_on_5xx(fake_data):
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIStatusError(
        message="server error",
        response=MagicMock(status_code=503),
        body=None,
    )
    failure.status_code = 503
    fake_client.messages.create = AsyncMock(
        side_effect=[failure, _make_anthropic_response("## AAPL\nfine")]
    )
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "## AAPL" in section
    assert fake_client.messages.create.await_count == 2


async def test_analyze_ticker_returns_failure_section_after_retry_exhausted(fake_data):
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIConnectionError(request=MagicMock())
    fake_client.messages.create = AsyncMock(side_effect=[failure, failure])
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "AAPL" in section
    assert "分析失败" in section


async def test_analyze_ticker_does_not_retry_on_4xx(fake_data):
    """4xx (e.g. 401 bad API key) must fail fast — single attempt, distinct placeholder."""
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIStatusError(
        message="invalid x-api-key",
        response=MagicMock(status_code=401),
        body=None,
    )
    # Patch status_code attribute since SDK reads it from response
    failure.status_code = 401
    fake_client.messages.create = AsyncMock(side_effect=failure)
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "AAPL" in section
    assert "配置错误" in section
    assert "401" in section
    # Must NOT have retried
    assert fake_client.messages.create.await_count == 1


async def test_analyze_ticker_retries_on_429_rate_limit(fake_data):
    """429 IS retriable (transient rate limit). Must attempt twice."""
    import anthropic
    fake_client = MagicMock()
    failure = anthropic.APIStatusError(
        message="rate limited",
        response=MagicMock(status_code=429),
        body=None,
    )
    failure.status_code = 429
    fake_client.messages.create = AsyncMock(
        side_effect=[failure, _make_anthropic_response("## AAPL\nfine")]
    )
    section = await analyst.analyze_ticker(
        client=fake_client,
        system_prompt="<sp>",
        data=fake_data,
        semaphore=asyncio.Semaphore(1),
    )
    assert "## AAPL" in section
    assert fake_client.messages.create.await_count == 2


def test_extract_text_handles_mixed_blocks():
    """Web-search responses include server-tool-use blocks; we want concatenated text only."""
    text_block = MagicMock(type="text", text="Hello.")
    tool_block = MagicMock(type="server_tool_use")
    other_text = MagicMock(type="text", text=" World.")
    response = MagicMock()
    response.content = [text_block, tool_block, other_text]
    # No "## " heading anywhere → return concatenated text as-is.
    assert analyst._extract_text(response) == "Hello. World."


def test_extract_text_strips_preamble_before_first_h3():
    """LLM occasionally emits 'Let me research...' before the actual prose.
    _extract_text must drop that and start at the first H3 (the prose template
    now starts with ### 公司速览, not a ticker H2)."""
    block = MagicMock(
        type="text",
        text=(
            "I'll research ENTG's qualitative aspects with targeted searches.\n"
            "I have sufficient information. Let me generate the report.\n"
            "### 公司速览\n\n"
            "Entegris is...\n"
        ),
    )
    response = MagicMock()
    response.content = [block]
    out = analyst._extract_text(response)
    assert out.startswith("### 公司速览")
    assert "I'll research" not in out
    assert "Let me generate" not in out


def test_extract_text_keeps_text_when_h3_is_first_line():
    block = MagicMock(type="text", text="### 公司速览\n\nbody")
    response = MagicMock()
    response.content = [block]
    assert analyst._extract_text(response).startswith("### 公司速览")


def test_extract_text_back_compat_strips_preamble_before_h2():
    """Older LLM outputs may still emit a ticker H2 first; strip preamble before that too."""
    block = MagicMock(
        type="text",
        text=(
            "Let me think.\n"
            "## AAPL — Apple Inc.\n\n"
            "### 公司速览\nbody\n"
        ),
    )
    response = MagicMock()
    response.content = [block]
    out = analyst._extract_text(response)
    assert out.startswith("## AAPL")
