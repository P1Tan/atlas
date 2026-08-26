import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol

from openai import OpenAI
from pydantic import BaseModel

from app.config import CHAT_MODEL

logger = logging.getLogger("atlas.chat")

SYSTEM_PROMPT = (
    "You are Atlas, a helpful personal assistant. Be concise and direct. "
    "Use the available tools when they let you answer more accurately or "
    "take a real action on the user's behalf; otherwise just reply in plain "
    "text."
)

# Safety cap on the tool-call loop -- a model that keeps calling tools
# forever (or a broken tool that keeps getting re-invoked) must not hang a
# request indefinitely.
MAX_TOOL_ITERATIONS = 5


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    # Display-only bookkeeping (which tool a "tool" message came from) --
    # not part of the OpenAI wire format, see to_openai().
    name: Optional[str] = None

    def to_openai(self) -> Dict[str, Any]:
        message: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            message["content"] = self.content
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.tool_calls is not None:
            message["tool_calls"] = self.tool_calls
        return message


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ChatEngine(Protocol):
    model_name: str

    def run_turn(
        self, messages: List[ChatMessage], tools: List[ToolDefinition]
    ) -> List[ChatMessage]: ...


class OpenAIChatEngine:
    """Runs one user turn to completion: calls the model, executes any tool
    calls it makes, feeds the results back, and repeats until the model
    replies with plain text (or MAX_TOOL_ITERATIONS is hit).

    Returns only the messages generated during this turn (assistant/tool
    messages) -- the caller already has everything before that.
    """

    def __init__(self, model_name: str = CHAT_MODEL, client: Optional[OpenAI] = None) -> None:
        self.model_name = model_name
        self._client = client or OpenAI()

    def run_turn(
        self, messages: List[ChatMessage], tools: List[ToolDefinition]
    ) -> List[ChatMessage]:
        tools_by_name = {tool.name: tool for tool in tools}
        openai_tools = [tool.to_openai_schema() for tool in tools] if tools else None

        working_messages = list(messages)
        new_messages: List[ChatMessage] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            kwargs: Dict[str, Any] = {
                "model": self.model_name,
                "messages": [m.to_openai() for m in working_messages],
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = self._client.chat.completions.create(**kwargs)
            choice_message = response.choices[0].message
            tool_calls = choice_message.tool_calls or []

            assistant_message = ChatMessage(
                role="assistant",
                content=choice_message.content,
                tool_calls=([tc.model_dump() for tc in tool_calls] if tool_calls else None),
            )
            working_messages.append(assistant_message)
            new_messages.append(assistant_message)

            if not tool_calls:
                return new_messages

            for call in tool_calls:
                tool = tools_by_name.get(call.function.name)
                if tool is None:
                    result: Any = {"error": f"unknown tool '{call.function.name}'"}
                else:
                    try:
                        arguments = json.loads(call.function.arguments)
                        result = tool.handler(arguments)
                    except Exception:
                        logger.exception("tool '%s' failed", call.function.name)
                        result = {"error": f"tool '{call.function.name}' failed"}

                tool_message = ChatMessage(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.function.name,
                    content=json.dumps(result, default=str),
                )
                working_messages.append(tool_message)
                new_messages.append(tool_message)

        logger.warning("chat turn hit max tool iterations (%d)", MAX_TOOL_ITERATIONS)
        return new_messages


def get_default_chat_engine() -> ChatEngine:
    return OpenAIChatEngine()


def get_chat_engine() -> ChatEngine:
    """FastAPI dependency -- the one function every route depending on a
    chat engine should use, mirroring app.extraction.get_extractor so
    dependency_overrides actually takes effect in tests."""
    return get_default_chat_engine()
