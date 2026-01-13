"""
Configuration management using environment variables.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


# Load .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class Config:
    """Application configuration"""
    
    # Environment
    BOT_MODE: str = os.getenv("BOT_MODE", "testnet")  # testnet or mainnet
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Binance
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    
    # KuCoin
    KUCOIN_API_KEY: str = os.getenv("KUCOIN_API_KEY", "")
    KUCOIN_SECRET_KEY: str = os.getenv("KUCOIN_SECRET_KEY", "")
    KUCOIN_PASSPHRASE: str = os.getenv("KUCOIN_PASSPHRASE", "")
    
    # Bybit
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_SECRET_KEY: str = os.getenv("BYBIT_SECRET_KEY", "")
    
    # OKX
    OKX_API_KEY: str = os.getenv("OKX_API_KEY", "")
    OKX_SECRET_KEY: str = os.getenv("OKX_SECRET_KEY", "")
    OKX_PASSPHRASE: str = os.getenv("OKX_PASSPHRASE", "")
    
    # Gate.io
    GATE_API_KEY: str = os.getenv("GATE_API_KEY", "")
    GATE_SECRET_KEY: str = os.getenv("GATE_SECRET_KEY", "")
    
    # BingX
    BINGX_API_KEY: str = os.getenv("BINGX_API_KEY", "")
    BINGX_SECRET_KEY: str = os.getenv("BINGX_SECRET_KEY", "")
    
    # Bot settings
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "100"))
    
    # Risk management
    DEFAULT_STOP_LOSS_PERCENT: float = float(os.getenv("DEFAULT_STOP_LOSS_PERCENT", "20"))
    DEFAULT_TAKE_PROFIT_PERCENT: float = float(os.getenv("DEFAULT_TAKE_PROFIT_PERCENT", "20"))
    
    # Directories
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    
    @classmethod
    def is_testnet(cls) -> bool:
        """Check if running in testnet mode"""
        return cls.BOT_MODE.lower() == "testnet"
    
    @classmethod
    def is_mainnet(cls) -> bool:
        """Check if running in mainnet mode"""
        return cls.BOT_MODE.lower() == "mainnet"
    
    @classmethod
    def validate(cls) -> tuple[bool, list[str]]:
        """
        Validate configuration
        
        Returns:
            Tuple of (is_valid, errors)
        """
        errors = []
        
        # Check API keys (warn if not configured, but don't fail)
        if not cls.BINANCE_API_KEY or not cls.BINANCE_SECRET_KEY:
            errors.append("Binance API credentials not configured")
        
        if not cls.BYBIT_API_KEY or not cls.BYBIT_SECRET_KEY:
            errors.append("Bybit API credentials not configured")
        
        # Log level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        if cls.LOG_LEVEL not in valid_log_levels:
            errors.append(f"Invalid LOG_LEVEL: {cls.LOG_LEVEL}")
        
        # Mode
        valid_modes = ["testnet", "mainnet"]
        if cls.BOT_MODE.lower() not in valid_modes:
            errors.append(f"Invalid BOT_MODE: {cls.BOT_MODE}")
        
        return (len(errors) == 0, errors)


# Global config instance
config = Config()
