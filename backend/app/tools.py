from app.chat import ToolDefinition

# Populated as product tools are added (Milestone 4.3 onward). Empty for now
# -- the chat endpoint still works as a plain multi-turn conversation with
# no tools available.
REGISTERED_TOOLS: list[ToolDefinition] = []
