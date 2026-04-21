import asyncio
import os
import sys
from dotenv import load_dotenv
from app.agent.mcp_agent import MCPAgent

# 1. Load the .env file automatically
load_dotenv()

async def main():
    # 2. Setup API Key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ Error: ANTHROPIC_API_KEY not found in .env file or environment.")
        return

    agent = MCPAgent(api_key=api_key)
    
    try:
        # 3. Open MCP Connection
        await agent.connect()
        print("\n🚀 Claude Agent with MCP Ready")
        print("💡 Mode: Live Search (DuckDuckGo)")
        print("👋 Type 'exit' or 'quit' to stop.\n")

        # 4. Chat Loop
        while True:
            # We use to_thread to prevent the input from blocking the async event loop
            # This is cleaner if you later add background heartbeats or tasks
            user_input = await asyncio.to_thread(input, "You: ")
            
            user_input = user_input.strip()
            if not user_input: 
                continue
            if user_input.lower() in ["exit", "quit"]: 
                break

            # 5. Call the agent thinking loop
            # This handles history, tool calls, and final response
            response = await agent.run_agent(user_input)
            
            print(f"\nAgent: {response}\n")

    except KeyboardInterrupt:
        print("\n\n[System] Interrupted by user.")
    except Exception as e:
        print(f"\n❌ An error occurred: {e}")
    finally:
        # 6. Cleanup
        print("🔌 Closing MCP connections...")
        await agent.disconnect()
        print("✅ Done.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Silences the stack trace on Ctrl+C
        sys.exit(0)