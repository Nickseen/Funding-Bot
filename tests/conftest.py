"""Pytest configuration and fixtures"""

import pytest
import asyncio
from src.core.state import AppState


@pytest.fixture
def app_state():
    """Create fresh AppState instance for testing"""
    return AppState()


@pytest.fixture
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
