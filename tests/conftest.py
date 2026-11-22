"""Tests never touch the real Windows Credential Manager (keys, trial count)."""
import os

os.environ["CLIPSTUDIO_VAULT"] = "memory"

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_vault():
    from clipstudio import vault

    if vault._MEMORY is not None:
        vault._MEMORY.clear()
    yield
