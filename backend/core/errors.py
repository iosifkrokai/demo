"""Typed errors raised by the layers and translated to HTTP by the routers."""

from __future__ import annotations


class ItinerariesUnavailableError(RuntimeError):
    """The committed itineraries file is missing or malformed."""


class AgentError(Exception):
    """Base class for all agent errors. HTTP status is .http_status."""

    http_status: int = 500


class NoRoutePossibleError(AgentError):
    http_status = 422


class UpstreamUnavailableError(AgentError):
    """Valhalla / DB / external is down or timing out."""

    http_status = 503


class InterpretationUnavailableError(AgentError):
    """The reading model is not configured, or did not answer."""

    http_status = 503
