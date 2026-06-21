import asyncio
from core.base_agent import BaseAgent
from utils.logger import get_logger

logger = get_logger()

class SentinelAgent(BaseAgent):
    def __init__(self, config=None):
        super().__init__(name="sentinel")
        self.config = config or {}

    async def decide(self, update, context):
        return "Sentinel: Monitoring mempool for whale activity."

    async def start(self):
        logger.info("SentinelAgent: Mempool stream initialized.")
        # Logic to connect to Flashbots/BloXroute would go here
