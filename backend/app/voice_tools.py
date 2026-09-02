import asyncio
from typing import List

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams

from app.chat import ToolDefinition


def to_function_schema(tool: ToolDefinition) -> FunctionSchema:
    """Adapt one of this codebase's ToolDefinition instances (see app.tools)
    into Pipecat's FunctionSchema, reusing the exact same handler -- same
    persona/tool behavior as text chat, not reimplemented.

    tool.handler is a plain synchronous function, and some of them do real
    blocking network I/O (remember_fact/search_facts call Supabase and
    OpenAI's embeddings API; get_weather/web_search call external APIs).
    Pipecat's pipeline runs on asyncio, so calling a blocking handler
    directly here would stall the entire event loop -- every concurrent
    thing the voice pipeline is doing -- for the duration of that I/O. Run
    it via asyncio.to_thread(...) instead, so the event loop stays free.
    """

    async def handler(params: FunctionCallParams) -> None:
        result = await asyncio.to_thread(tool.handler, dict(params.arguments))
        await params.result_callback(result)

    return FunctionSchema(
        name=tool.name,
        description=tool.description,
        properties=tool.parameters["properties"],
        required=tool.parameters["required"],
        handler=handler,
    )


def to_function_schemas(tools: List[ToolDefinition]) -> List[FunctionSchema]:
    return [to_function_schema(tool) for tool in tools]
