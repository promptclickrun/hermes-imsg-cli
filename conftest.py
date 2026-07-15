"""Pytest bootstrap: ensure the repo root (for ``hermes_imsg_cli``) and an
optional Hermes source tree are importable.

Adapter / registration tests need Hermes core (``gateway``, ``hermes_cli``) on
``sys.path``.  Provide it either via ``PYTHONPATH`` or by setting ``HERMES_SRC``
to a Hermes checkout; the pure ``rules`` / ``config`` / ``cli`` tests need
neither.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_hermes = os.environ.get("HERMES_SRC")
if _hermes and os.path.isdir(_hermes) and _hermes not in sys.path:
    sys.path.insert(1, _hermes)
