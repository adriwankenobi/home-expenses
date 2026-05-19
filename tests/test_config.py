from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from home_expenses.config import (
    Category,
    Config,
    ConfigError,
    load_config,
)


def _write_config(tmp_path: Path, payload: dict[str, Any]) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _valid_payload(tmp_path: Path) -> dict[str, Any]:
    statements_dir = tmp_path / "bank"
    statements_dir.mkdir()
    invoice_dir = tmp_path / "pepe"
    invoice_dir.mkdir()
    return {
        "currency": "EUR",
        "bank_statements": {"dir": str(statements_dir), "glob": "*.csv"},
        "match_window_days_default": 30,
        "categories": {
            "electricity": {
                "invoice_folder": str(invoice_dir),
                "invoice_parser": "pepeenergy",
                "recurrence": "monthly",
                "patterns": ["RECIBO PEPE ENERGY"],
            },
        },
        "manual_mappings": {},
    }


def test_load_valid_config(tmp_path: Path) -> None:
    p = _write_config(tmp_path, _valid_payload(tmp_path))
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.currency == "EUR"
    elec: Category = cfg.categories["electricity"]
    assert elec.invoice_parser == "pepeenergy"
    assert elec.recurrence == "monthly"


def test_missing_required_field_raises(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    del payload["bank_statements"]
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="bank_statements"):
        load_config(p)


def test_bank_statements_dir_must_exist(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["bank_statements"]["dir"] = str(tmp_path / "does_not_exist")
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(p)


def test_invoice_folder_must_exist(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["invoice_folder"] = str(tmp_path / "nope")
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="nope"):
        load_config(p)


def test_invoice_folder_requires_parser(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    del payload["categories"]["electricity"]["invoice_parser"]
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="invoice_parser"):
        load_config(p)


def test_invalid_recurrence_value(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["recurrence"] = "weekly"
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="recurrence"):
        load_config(p)


def test_duplicate_pattern_across_categories_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["other"] = {
        "patterns": ["RECIBO PEPE ENERGY"],
        "recurrence": "monthly",
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(p)


def test_manual_mapping_collides_with_pattern_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"RECIBO PEPE ENERGY": "electricity"}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="collide"):
        load_config(p)


def test_cache_dir_defaults_to_sibling_of_config(tmp_path: Path) -> None:
    p = _write_config(tmp_path, _valid_payload(tmp_path))
    cfg = load_config(p)
    assert cfg.cache_dir == tmp_path / "cache"
