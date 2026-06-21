import structlog
import logging

def setup_logging(level: str):
    logging.basicConfig(level=level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ]
    )
    return structlog.get_logger()

def get_logger():
    return structlog.get_logger()
