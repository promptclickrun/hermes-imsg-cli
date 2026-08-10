"""Shared pytest isolation for plugin configuration environment variables."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_imsg_environment(monkeypatch):
    """Keep the operator's live iMessage policy out of unit-test defaults."""
    for name in tuple(os.environ):
        if name.startswith("IMSG_"):
            monkeypatch.delenv(name, raising=False)
