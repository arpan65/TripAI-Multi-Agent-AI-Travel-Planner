from anthropic import Anthropic
from app.core.config import ANTHROPIC_API_KEY
from duckduckgo_search import DDGS

client = Anthropic(api_key=ANTHROPIC_API_KEY)
# ------------------------
# TOOL IMPLEMENTATION
# ------------------------
def calculator(operation: str, a: float, b: float) -> float:
    if operation == "add":
        return a + b
    elif operation == "subtract":
        return a - b
    elif operation == "multiply":
        return a * b
    elif operation == "divide":
        return a / b
    else:
        return "Invalid operation"



# ------------------------
# TOOL SCHEMA (VERY IMPORTANT)
# ------------------------
tools = [
    {
        "name": "calculator",
        "description": "Perform basic arithmetic operations",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide"]
                },
                "a": {"type": "number"},
                "b": {"type": "number"}
            },
            "required": ["operation", "a", "b"]
        }
    }

]

conversation_history = []
def run_agent(user_input: str):
    global conversation_history
    # Keep the most recent 10 messages, ensuring we start with a 'user' message
    if len(conversation_history) > 15:
        conversation_history = conversation_history[-15:]
        # Ensure the history starts with a 'user' role
        while conversation_history and conversation_history[0]["role"] != "user":
            conversation_history.pop(0)
    conversation_history.append({"role": "user", "content": user_input})
    while True:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=conversation_history,
            tools=tools        )

        # CRITICAL: Add the assistant's response to history immediately
        conversation_history.append({
            "role": "assistant",
            "content": response.content
        })

        found_tool_use = False
        final_text = ""

        # LOOP through all blocks in the response
        for block in response.content:
            if block.type == "text":
                final_text = block.text
            
            elif block.type == "tool_use":
                found_tool_use = True
                print(f"\n[Tool Call] {block.name} with {block.input}")

                if block.name == "calculator":
                    result = calculator(**block.input)    
                else:
                    result = "Unknown tool"

                # Provide the tool result back to history
                conversation_history.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result)
                        }
                    ]
                })

        # If Claude didn't want to use a tool, return the text and stop the 'while' loop
        if not found_tool_use:
            return final_text