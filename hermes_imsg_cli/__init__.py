"""imsg-platform — standalone Hermes iMessage/SMS gateway platform plugin.

The plugin entry point is :func:`register`.  The heavy adapter import is
deferred until ``register(ctx)`` is actually called by the Hermes plugin loader
so that ``import hermes_imsg_cli.rules`` (and the other Hermes-free helpers) work
without a full Hermes checkout on ``PYTHONPATH``.
"""

from __future__ import annotations

__all__ = ["register"]
__version__ = "1.0.0"


def register(ctx) -> None:
    """Hermes plugin entry point — registers the ``imsg`` platform adapter."""
    from .adapter import register as _register
    _register(ctx)
