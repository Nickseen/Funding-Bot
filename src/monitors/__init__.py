"""
Monitors - Мониторинг и детекция событий.

Компоненты:
- FundingTracker: Мониторинг фандинга и автозакрытие при потере выгодности
- EmergencyMonitor: Детекция SL/TP срабатываний (будет добавлен)
"""

from .funding_tracker import FundingTracker

__all__ = [
    "FundingTracker",
]
