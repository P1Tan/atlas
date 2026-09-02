import asyncio
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.chat import ToolDefinition
from app.voice_tools import to_function_schema, to_function_schemas


def _make_tool(handler=None) -> ToolDefinition:
    return ToolDefinition(
        name="get_weather",
        description="Get current weather conditions for a location.",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "A city or place name."}
            },
            "required": ["location"],
        },
        handler=handler or (lambda arguments: {"ok": True, "location": arguments["location"]}),
    )


class FakeFunctionCallParams:
    """Stands in for pipecat.services.llm_service.FunctionCallParams -- only
    the fields to_function_schema's generated handler actually reads."""

    def __init__(self, arguments: Dict[str, Any]) -> None:
        self.arguments = arguments
        self.result_callback = AsyncMock()


def test_to_function_schema_unwraps_name_description_properties_required() -> None:
    tool = _make_tool()

    schema = to_function_schema(tool)

    assert schema.name == "get_weather"
    assert schema.description == "Get current weather conditions for a location."
    assert schema.properties == {
        "location": {"type": "string", "description": "A city or place name."}
    }
    assert schema.required == ["location"]


def test_to_function_schemas_maps_over_a_list() -> None:
    tools = [_make_tool(), _make_tool()]

    schemas = to_function_schemas(tools)

    assert len(schemas) == 2
    assert all(s.name == "get_weather" for s in schemas)


def test_handler_invokes_underlying_tool_handler_with_arguments_dict() -> None:
    received: List[Dict[str, Any]] = []

    def underlying_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        received.append(arguments)
        return {"ok": True, "echoed": arguments["location"]}

    tool = _make_tool(handler=underlying_handler)
    schema = to_function_schema(tool)
    params = FakeFunctionCallParams({"location": "Boston, MA"})

    asyncio.run(schema.handler(params))

    assert received == [{"location": "Boston, MA"}]


def test_handler_calls_result_callback_with_the_handlers_return_value() -> None:
    tool = _make_tool(handler=lambda arguments: {"ok": True, "location": arguments["location"]})
    schema = to_function_schema(tool)
    params = FakeFunctionCallParams({"location": "Tokyo"})

    asyncio.run(schema.handler(params))

    params.result_callback.assert_awaited_once_with({"ok": True, "location": "Tokyo"})


def test_handler_runs_the_blocking_tool_handler_via_asyncio_to_thread() -> None:
    # Prove the sync tool.handler is dispatched through asyncio.to_thread
    # (not called directly on the event loop) -- a mock/monkeypatch on
    # asyncio.to_thread is a robust way to confirm this without relying on
    # flaky timing assertions.
    tool = _make_tool()
    schema = to_function_schema(tool)
    params = FakeFunctionCallParams({"location": "Paris"})

    real_to_thread = asyncio.to_thread
    with patch("app.voice_tools.asyncio.to_thread", wraps=real_to_thread) as mock_to_thread:
        asyncio.run(schema.handler(params))

    mock_to_thread.assert_called_once()
    called_fn = mock_to_thread.call_args.args[0]
    assert called_fn is tool.handler


def test_handler_does_not_block_the_event_loop() -> None:
    # A tool handler that blocks (time.sleep) must not stall other coroutines
    # scheduled on the same event loop -- that's the entire point of running
    # it via asyncio.to_thread instead of calling it directly.
    def slow_handler(arguments: Dict[str, Any]) -> Dict[str, Any]:
        time.sleep(0.2)
        return {"ok": True}

    tool = _make_tool(handler=slow_handler)
    schema = to_function_schema(tool)
    params = FakeFunctionCallParams({"location": "Nowhere"})

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks += 1

    async def run_both() -> None:
        await asyncio.gather(schema.handler(params), ticker())

    asyncio.run(run_both())

    # If the handler had blocked the loop directly, the ticker would have
    # gotten essentially zero ticks in while the 0.2s sleep ran.
    assert ticks > 5


def test_required_and_properties_come_from_tool_parameters_not_the_full_schema() -> None:
    tool = ToolDefinition(
        name="set_reminder",
        description="Set a reminder.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date_phrase": {"type": "string"},
            },
            "required": ["title", "date_phrase"],
        },
        handler=lambda arguments: {"ok": True},
    )

    schema = to_function_schema(tool)

    assert schema.properties == {
        "title": {"type": "string"},
        "date_phrase": {"type": "string"},
    }
    assert schema.required == ["title", "date_phrase"]


def test_handler_is_pytest_marked_async_compatible() -> None:
    # Sanity check that the generated handler is itself a coroutine function,
    # matching pipecat's FunctionCallHandler = Callable[[params], Awaitable[None]].
    tool = _make_tool()
    schema = to_function_schema(tool)

    assert asyncio.iscoroutinefunction(schema.handler)
