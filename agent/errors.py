"""Typed errors raised by the planner and translated to HTTP by main.py."""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all agent errors. HTTP status is .http_status."""
    http_status: int = 500


class InvalidRequest(AgentError):
    http_status = 422


class NoCandidatesFound(AgentError):
    http_status = 404


class NoRoutePossible(AgentError):
    http_status = 422


class UpstreamUnavailable(AgentError):
    """Valhalla / DB / external is down or timing out."""
    http_status = 503


class ModelNotReady(AgentError):
    """A heavy resource (LLM, embedder) has not loaded yet."""
    http_status = 503
