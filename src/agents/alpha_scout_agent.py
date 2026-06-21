import asyncio
from core.base_agent import BaseAgent
from utils.logger import get_logger

logger = get_logger()

class AlphaScoutAgent(BaseAgent):
    def __init__(self, config=None):
        super().__init__(name="alpha_scout")
        self.config = config or {}

    async def decide(self, update, context):
        return "AlphaScout: Tracking insider movements and sentiment."

    async def start(self):
        logger.info("AlphaScoutAgent: Social/On-chain stream initialized.")
