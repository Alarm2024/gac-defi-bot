from pydantic_settings import BaseSettings
from enum import Enum

class HackathonTarget(str, Enum):
    ZERO_CUP = "zero_cup"
    QWEN = "qwen"

class Settings(BaseSettings):
    hackathon_target: HackathonTarget = HackathonTarget.ZERO_CUP
    telegram_token: str
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        env_prefix = "GUARDIAN_"
