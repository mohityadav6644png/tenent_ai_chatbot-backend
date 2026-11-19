"""
Application Monitoring (app/infrastructure/azure/monitoring.py)
Telemetry and metric collection tracking API requests and latency metrics.
"""

from typing import Dict, Optional
from app.core.logging import logger


class TelemetryTracker:
    def __init__(self, app_insights_key: Optional[str] = None):
        self.app_insights_key = app_insights_key

    def track_metric(self, name: str, value: float, properties: Optional[Dict[str, str]] = None) -> None:
        logger.info(f"[METRIC] {name} = {value} | {properties or {}}")

    def track_event(self, name: str, properties: Optional[Dict[str, str]] = None) -> None:
        logger.info(f"[EVENT] {name} | {properties or {}}")
