"""The MCP server exposes the research tools with schemas and structured output."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mcp import Client

from mcp_server import build_server

EXPECTED_TOOLS = {"get_regime", "run_backtest", "get_forward_returns",
                  "detect_peaks", "search_fed_docs"}


def run(coro):
    return asyncio.run(coro)


async def _list_tools():
    async with Client(build_server()) as client:
        return (await client.list_tools()).tools


async def _call(name, arguments):
    async with Client(build_server()) as client:
        return await client.call_tool(name, arguments)


def test_every_tool_is_listed_with_required_as_of_and_output_schema():
    tools = {t.name: t for t in run(_list_tools())}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert "as_of" in tool.input_schema["required"]
        assert tool.output_schema is not None
        assert "as_of" in tool.output_schema["properties"]
        assert tool.annotations.read_only_hint is True


def test_call_returns_structured_content():
    result = run(_call("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}))
    assert not result.is_error
    data = result.structured_content
    assert data["market"] == "QQQ"
    assert data["data_through"] <= "2022-10-13"
    assert data["regime"] in {"panic", "greed", "neutral"}


def test_invalid_arguments_are_reported_to_the_client():
    result = run(_call("get_regime", {"market": "NOPE", "as_of": "2022-10-13"}))
    assert result.is_error


def test_tool_input_errors_keep_their_message():
    result = run(_call("get_regime", {"market": "QQQ", "as_of": "2010-01-01"}))
    assert result.is_error
    assert "before the first available observation" in result.content[0].text
