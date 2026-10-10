"""The interpretation agent package.

``agent.client`` is the public entry point; ``interpret_cache`` is re-exported
here under its historical name so ``from agent import interpret_cache`` keeps
working.
"""

from agent import cache as interpret_cache
from agent.client import available, interpret_with_agent

__all__ = ["available", "interpret_cache", "interpret_with_agent"]
