"""Typed errors raised by the layers and translated to HTTP by the routers."""

from __future__ import annotations


class ItinerariesUnavailable(RuntimeError):
    """The committed itineraries file is missing or malformed.

    A deployment bug rather than a request error, which is why it is not an
    `AgentError`: the router answers 503 and the operator fixes the file.
    """


class AgentError(Exception):
    """Base class for all agent errors. HTTP status is .http_status."""

    http_status: int = 500


class NoCandidatesFound(AgentError):
    http_status = 404


class NoRoutePossible(AgentError):
    http_status = 422


class UpstreamUnavailable(AgentError):
    """Valhalla / DB / external is down or timing out."""

    http_status = 503


class InterpretationUnavailable(AgentError):
    """The reading model is not configured, or did not answer."""

    http_status = 503
