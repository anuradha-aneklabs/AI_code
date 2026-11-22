"""Tests for CLI discovery template-default refusal (Part 1e)."""

import argparse
import pytest

from harvey.cli import cmd_discover


class DummyArgs:
    def __init__(self, **kwargs):
        self.providers = False
        self.provider = "osm"
        self.estimate = False
        self.city = None
        self.industry = None
        self.depth = 1
        self.limit = 5
        self.preset = None
        self.max_spend = 0.0
        self.no_profile = True
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_cmd_discover_refuses_template_default_without_cli_industry(capsys, monkeypatch):
    """cmd_discover must refuse live discovery with SystemExit(1) if default template criteria is used."""
    from harvey import config
    monkeypatch.setattr(config, "_find_config_file", lambda: "harvey.yaml")

    args = DummyArgs(provider="osm", estimate=False, industry=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_discover(args)

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "ERROR: Refusing live discovery with empty or template-default criteria" in captured.out
    assert "harvey discover --industry <your-industry>" in captured.out
    assert "harvey.local.yaml" in captured.out


def test_cmd_discover_allows_estimate_with_template_default(capsys, monkeypatch):
    """cmd_discover allows --estimate even if default template criteria is present."""
    from harvey import config
    monkeypatch.setattr(config, "_find_config_file", lambda: "harvey.yaml")

    args = DummyArgs(provider="osm", estimate=True, industry=None)
    cmd_discover(args)
    captured = capsys.readouterr()
    assert "Estimate only" in captured.out
