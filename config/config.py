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
    # KuCoin can run in its own mode independent of BOT_MODE: "mainnet", "testnet", or "auto".
    # "auto" means follow global BOT_MODE.
    KUCOIN_MODE: str = os.getenv("KUCOIN_MODE", "auto").lower()
    # Use KuCoin test-order endpoint on mainnet to validate API integration
    KUCOIN_TEST_ORDERS: bool = os.getenv("KUCOIN_TEST_ORDERS", "true").lower() == "true"
    
    # Bybit
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_SECRET_KEY: str = os.getenv("BYBIT_SECRET_KEY", "")
    
    # OKX
    OKX_API_KEY: str = os.getenv("OKX_API_KEY", "")
    OKX_SECRET_KEY: str = os.getenv("OKX_SECRET_KEY", "")
    OKX_PASSPHRASE: str = os.getenv("OKX_PASSPHRASE", "")
    # OKX can run in its own mode independent of BOT_MODE: "mainnet", "testnet", or "auto".
    # "auto" means follow global BOT_MODE.
    OKX_MODE: str = os.getenv("OKX_MODE", "auto").lower()
    
    # Gate.io
    GATE_API_KEY: str = os.getenv("GATE_API_KEY", "")
    GATE_SECRET_KEY: str = os.getenv("GATE_SECRET_KEY", "")
    
    # BingX
    BINGX_API_KEY: str = os.getenv("BINGX_API_KEY", "")
    BINGX_SECRET_KEY: str = os.getenv("BINGX_SECRET_KEY", "")
    # BingX can run in its own mode independent of BOT_MODE: "mainnet", "testnet", or "auto".
    # "auto" means follow global BOT_MODE.
    BINGX_MODE: str = os.getenv("BINGX_MODE", "auto").lower()
    # Settlement/balance asset switch for BingX:
    # - auto: VST in testnet, USDT in mainnet
    # - vst: force Virtual USDT (demo)
    # - usdt: force real USDT
    BINGX_SETTLEMENT_ASSET: str = os.getenv("BINGX_SETTLEMENT_ASSET", "auto").lower()
    
    # Bitget
    BITGET_API_KEY: str = os.getenv("BITGET_API_KEY", "")
    BITGET_SECRET_KEY: str = os.getenv("BITGET_SECRET_KEY", "")
    BITGET_PASSPHRASE: str = os.getenv("BITGET_PASSPHRASE", "")
    
    # MEXC
    MEXC_API_KEY: str = os.getenv("MEXC_API_KEY", "")
    MEXC_SECRET_KEY: str = os.getenv("MEXC_SECRET_KEY", "")
    
    # Lighter (zkLighter DEX)
    LIGHTER_API_KEY: str = os.getenv("LIGHTER_API_KEY", "")  # API public key (hex)
    LIGHTER_SECRET_KEY: str = os.getenv("LIGHTER_SECRET_KEY", "")  # API private key (hex)
    LIGHTER_ACCOUNT_INDEX: int = int(os.getenv("LIGHTER_ACCOUNT_INDEX", "0"))
    LIGHTER_API_KEY_INDEX: int = int(os.getenv("LIGHTER_API_KEY_INDEX", "0"))
    
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
    def is_kucoin_testnet(cls) -> bool:
        """Check if KuCoin should run in testnet mode."""
        if cls.KUCOIN_MODE == "testnet":
            return True
        if cls.KUCOIN_MODE == "mainnet":
            return False
        return cls.is_testnet()

    @classmethod
    def is_kucoin_mainnet(cls) -> bool:
        """Check if KuCoin should run in mainnet mode."""
        return not cls.is_kucoin_testnet()

    @classmethod
    def is_okx_testnet(cls) -> bool:
        """Check if OKX should run in testnet mode."""
        if cls.OKX_MODE == "testnet":
            return True
        if cls.OKX_MODE == "mainnet":
            return False
        return cls.is_testnet()

    @classmethod
    def is_okx_mainnet(cls) -> bool:
        """Check if OKX should run in mainnet mode."""
        return not cls.is_okx_testnet()

    @classmethod
    def is_bingx_testnet(cls) -> bool:
        """Check if BingX should run in testnet/demo mode."""
        if cls.BINGX_MODE == "testnet":
            return True
        if cls.BINGX_MODE == "mainnet":
            return False
        return cls.is_testnet()

    @classmethod
    def is_bingx_mainnet(cls) -> bool:
        """Check if BingX should run in mainnet mode."""
        return not cls.is_bingx_testnet()
    
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

        valid_kucoin_modes = ["auto", "testnet", "mainnet"]
        if cls.KUCOIN_MODE not in valid_kucoin_modes:
            errors.append(f"Invalid KUCOIN_MODE: {cls.KUCOIN_MODE}")

        valid_okx_modes = ["auto", "testnet", "mainnet"]
        if cls.OKX_MODE not in valid_okx_modes:
            errors.append(f"Invalid OKX_MODE: {cls.OKX_MODE}")

        valid_bingx_modes = ["auto", "testnet", "mainnet"]
        if cls.BINGX_MODE not in valid_bingx_modes:
            errors.append(f"Invalid BINGX_MODE: {cls.BINGX_MODE}")

        valid_bingx_assets = ["auto", "vst", "usdt"]
        if cls.BINGX_SETTLEMENT_ASSET not in valid_bingx_assets:
            errors.append(
                f"Invalid BINGX_SETTLEMENT_ASSET: {cls.BINGX_SETTLEMENT_ASSET}"
            )
        
        return (len(errors) == 0, errors)


# Global config instance
config = Config()
