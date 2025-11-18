from dataclasses import dataclass, field
from typing import cast
import openai
from openai.types import ChatModel
from dotenv import load_dotenv
from mcp import ClientSession
import json
from mcp.client.stdio import stdio_client
from mcp import ClientSession, StdioServerParameters
import logging
import traceback
import os
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

server_params = StdioServerParameters(
    command="python", 
    args=["./mcp_server.py"],  
    env=None, 
)


load_dotenv()

openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"),base_url=os.getenv("OPENAI_BASE_URL"))

def load_prompt(path):
    with open(path, "r") as f:
        return f.read()

PROMPT_PATH = "reservation_agent_prompt.md"

@dataclass
class ReservationAgent():
    user_id : int = 1
    messages: list[ChatModel] = field(default_factory=list)
    MAX_MEMORY: int = 10
  
    def __post_init__(self):
        system_prompt = load_prompt(PROMPT_PATH)
        self.messages.append({"role": "system", "content": system_prompt})

    def chat_history(self):
        system_msg = [msg for msg in self.messages if msg["role"] == "system"]
        user_assistant_msgs = [msg for msg in self.messages if msg["role"] in ("user", "assistant", "tool")]
        trimmed = user_assistant_msgs[-(self.MAX_MEMORY * 2):]  
        self.messages = system_msg + trimmed
    
    async def process_query(self, session: ClientSession, query: str) -> dict:
        try:
            # Get available tools from MCP server
            response = await session.list_tools()
            available_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                }
                for tool in response.tools
            ]
        except Exception as e:
            logger.error(f"Error fetching tools: {e}")
            return {"error": "Failed to fetch tools from backend."}
    
        try:
            # Send user query to LLM with tool schemas
            self.messages.append({"role": "user", "content": f"user_id : {self.user_id}, query : {query}"})

            res = await openai_client.chat.completions.create(
                model=os.getenv("MODEL"),
                messages=self.messages,
                tools=available_tools,
            )
            self.messages.append({"role": "assistant", "content": res.choices[0].message.content})

            # Handle possible tool calls
            while True:
                choice = res.choices[0].message
                
                # If model wants to call a tool
                if hasattr(choice, "tool_calls") and choice.tool_calls:
                    for tool_call in choice.tool_calls:
                        tool_name = tool_call.function.name
                        tool_args = json.loads(tool_call.function.arguments)

                        cleaned_args = {}
                        for key, value in tool_args.items():
                            if value not in [None, "Unknown", "null", "None", ""]:
                                cleaned_args[key] = value

                        # Run tool via MCP
                        result = await session.call_tool(tool_name, cast(dict, cleaned_args))
                        raw_text = result.content[0].text if result.content else "{}"

                        # Append tool result for the model to read
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": raw_text
                        })
    
                    # After appending tool result → call model again (NO tools passed here)
                    res = await openai_client.chat.completions.create(
                        model=os.getenv("MODEL"),
                        messages=self.messages,
                        tools=available_tools,
                    )

                    self.messages.append({"role": "assistant", "content": res.choices[0].message.content})
                    continue  # Continue loop: model may call another tool

                break

            self.chat_history()
    
            return {
                "response": res.choices[0].message.content,
                "context": self.messages,
            }
    
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            logger.error(traceback.format_exc())
            return {"error": "Failed to process query."}

    async def run_query(self, query: str) -> dict:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await self.process_query(session, query)

ai_agent = ReservationAgent()