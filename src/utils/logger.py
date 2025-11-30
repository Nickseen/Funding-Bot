"""
Logging configuration using loguru.

Logs are stored in files with automatic rotation (one file per day).
"""

import sys
from pathlib import Path
from loguru import logger
from datetime import datetime


def setup_logger(log_level: str = "INFO", log_dir: str = "logs"):
    """
    Setup logger with file rotation
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory to store logs
    """
    # Create logs directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)
    
    # Remove default handler
    logger.remove()
    
    # Add console handler with colors
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> | <level>{message}</level>",
        level=log_level,
        colorize=True,
    )
    
    # Add file handler with rotation (one file per day)
    logger.add(
        f"{log_dir}/bot_{{time:YYYY-MM-DD}}.log",
        rotation="00:00",  # Rotate at midnight
        retention="30 days",  # Keep logs for 30 days
        compression="zip",  # Compress old logs
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        level=log_level,
        enqueue=True,  # Async writing
    )
    
    # Add separate error log
    logger.add(
        f"{log_dir}/errors_{{time:YYYY-MM-DD}}.log",
        rotation="00:00",
        retention="90 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
        level="ERROR",
        enqueue=True,
    )
    
    logger.info(f"Logger initialized - Level: {log_level}, Log dir: {log_dir}")
    
    return logger


# Initialize logger
log = setup_logger()
