"""Deterministic UAV domain tools used by the Agent Engine POC."""

from .uav import register_uav_tools
from .future_battlefield import register_future_battlefield_tools
from .cognitive import register_cognitive_tools

__all__ = ["register_uav_tools", "register_future_battlefield_tools", "register_cognitive_tools"]
