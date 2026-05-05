"""Backward-compatible shim — MCPAgent re-exported from pipeline.TravelAgent."""
from .pipeline import TravelAgent as MCPAgent

__all__ = ["MCPAgent"]
