import logging
import os
from typing import Any, Dict, List
from groq import Groq

logger = logging.getLogger("BaseAgent")

class BaseAgent:
    def __init__(self, name: str):
        self.name = name
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        self.memory: List[Dict[str, str]] = []

    async def process(self, text: str) -> str:
        # Add to memory
        self.memory.append({"role": "user", "content": text})
        # Keep last 5 messages
        if len(self.memory) > 5:
            self.memory = self.memory[-5:]
        
        messages = [
            {"role": "system", "content": f"You are {self.name}, an intelligent assistant."}
        ] + self.memory

        completion = self.client.chat.completions.create(
            model="llama3-8b-8192",
            messages=messages
        )
        
        response = completion.choices[0].message.content
        self.memory.append({"role": "assistant", "content": response})
        
        return response

    async def decide(self, update: Any, context: Any) -> str:
        return f"{self.name} is active."

    async def invoke_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        return f"Tool {tool_name} invoked."
