"""Every per-user setting lives in one directory, found by one rule.

The rule was written out twice, in the cost model and the agent's model
choice, and a third module was about to need it. Two copies agree until one
is edited; these hold both callers to the one in ``user_dir`` by what they
return, whichever way the directory is chosen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fastmdxplora.agent.models import model_path
from fastmdxplora.cost import calibration_path
from fastmdxplora.user_dir import user_config_dir


@pytest.fixture
def clean_environment(monkeypatch):
    monkeypatch.delenv("FASTMDXPLORA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return monkeypatch


def test_the_override_moves_every_setting(clean_environment, tmp_path):
    clean_environment.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path))
    assert user_config_dir() == tmp_path
    assert calibration_path() == tmp_path / "calibration.json"
    assert model_path() == tmp_path / "model.json"


@pytest.mark.skipif(os.name == "nt", reason="XDG applies off Windows")
def test_the_platform_convention_is_followed(clean_environment, tmp_path):
    clean_environment.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_config_dir() == tmp_path / "fastmdxplora"
    assert calibration_path().parent == user_config_dir()
    assert model_path().parent == user_config_dir()


@pytest.mark.skipif(os.name == "nt", reason="XDG applies off Windows")
def test_the_home_directory_is_the_fallback(clean_environment):
    assert user_config_dir() == Path.home() / ".config" / "fastmdxplora"
