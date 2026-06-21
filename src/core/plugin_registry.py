import logging
from core.base_agent import BaseAgent
from agents.yield_arbitrage import YieldArbitrageAgent
from agents.sentinel_agent import SentinelAgent
from agents.dark_mirror_agent import DarkMirrorAgent
from agents.voyager_agent import VoyagerAgent
from agents.alpha_scout_agent import AlphaScoutAgent

logger = logging.getLogger("PluginRegistry")

class PluginRegistry:
    def __init__(self, redis_url: str, metrics_port: int = 9000, redis_retries: int = 5):
        self._redis_url = redis_url
        self._agents = {}
        # Initial registration for agents
        self.register("yield_arbitrage", YieldArbitrageAgent)
        self.register("sentinel", SentinelAgent)
        self.register("dark_mirror", DarkMirrorAgent)
        self.register("voyager", VoyagerAgent)
        self.register("alpha_scout", AlphaScoutAgent)

    async def start(self) -> None:
        logger.info(f"PluginRegistry connected via Redis: {self._redis_url}")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        logger.info("PluginRegistry closed")

    def register(self, key: str, agent: BaseAgent) -> None:
        self._agents[key] = agent
        logger.info(f"Registered agent: {key}")

    def get(self, key: str) -> BaseAgent:
        if key not in self._agents:
            raise KeyError(f"No agent registered under '{key}'")
        return self._agents[key]
