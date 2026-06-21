import logging
import os
from groq import Groq

logger = logging.getLogger("Orchestrator")

class Orchestrator:
    def __init__(self, target: str):
        self.target = target
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

    async def intent_routing(self, text: str) -> str:
        prompt = f"Given the user message: '{text}', decide if it should be handled by the 'qwen' agent or the 'zero_cup' agent. Reply ONLY with the name of the agent."
        completion = self.client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content.strip()

    async def handle_update(self, update, context, target: str) -> str:
        logger.info(f"Routing message to {target}")
        return f"System Online. The {target} agent received your message: {update.message.text}"

async def run_orchestrator(redis_url: str, target: str) -> Orchestrator:
    logger.info("Orchestrator initialized.")
    return Orchestrator(target=target)
