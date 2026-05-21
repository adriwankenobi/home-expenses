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


def test_manual_mapping_collides_with_pattern_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"RECIBO PEPE ENERGY": {"category": "electricity"}}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="collide"):
        load_config(p)


def test_cache_dir_defaults_to_sibling_of_config(tmp_path: Path) -> None:
    p = _write_config(tmp_path, _valid_payload(tmp_path))
    cfg = load_config(p)
    assert cfg.cache_dir == tmp_path / "cache"


def test_bank_statements_must_be_object(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["bank_statements"] = "oops"
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="bank_statements must be an object"):
        load_config(p)


def test_categories_must_be_object(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"] = [{"name": "x", "recurrence": "monthly"}]
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config(p)


def test_manual_mappings_must_be_object(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = []
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="manual_mappings must be an object"):
        load_config(p)


def test_start_date_monthly_parses_yyyy_mm(tmp_path: Path) -> None:
    from datetime import date as _date

    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["start_date"] = "2024-03"
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.categories["electricity"].start_date == _date(2024, 3, 1)


def test_start_date_monthly_rejects_day_format(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["start_date"] = "2024-03-15"
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="monthly recurrence"):
        load_config(p)


def test_start_date_yearly_parses_yyyy(tmp_path: Path) -> None:
    from datetime import date as _date

    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["recurrence"] = "yearly"
    payload["categories"]["electricity"]["start_date"] = "2024"
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.categories["electricity"].start_date == _date(2024, 1, 1)


def test_start_date_quarterly_parses_yyyy_qn(tmp_path: Path) -> None:
    from datetime import date as _date

    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["recurrence"] = "quarterly"
    payload["categories"]["electricity"]["start_date"] = "2024-Q3"
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    # Q3 starts in July.
    assert cfg.categories["electricity"].start_date == _date(2024, 7, 1)


def test_start_date_bimonthly_rejects_even_month(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["recurrence"] = "bimonthly"
    payload["categories"]["electricity"]["start_date"] = "2024-04"
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="bimonthly"):
        load_config(p)


def test_start_date_none_recurrence_parses_yyyy_mm_dd(tmp_path: Path) -> None:
    from datetime import date as _date

    payload = _valid_payload(tmp_path)
    payload["categories"]["electricity"]["recurrence"] = "none"
    payload["categories"]["electricity"]["start_date"] = "2024-03-15"
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.categories["electricity"].start_date == _date(2024, 3, 15)


# --- split groups ---


def _add_split_pair_no_invoices(
    payload: dict[str, Any], pattern: str = "RECIBO TRIMESTRAL"
) -> None:
    payload["categories"]["water"] = {"recurrence": "quarterly", "patterns": [pattern]}
    payload["categories"]["tax"] = {"recurrence": "quarterly", "patterns": [pattern]}


def test_shared_pattern_forms_split_group(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    _add_split_pair_no_invoices(payload)
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    grp = cfg.split_groups[0]
    assert tuple(m.name for m in grp.members) == ("water", "tax")  # config order
    assert grp.recurrence == "quarterly"
    assert grp.has_invoices is False


def test_split_group_with_invoices(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    inv_b = tmp_path / "inv_b"
    inv_b.mkdir()
    payload["categories"]["a"] = {
        "recurrence": "quarterly",
        "patterns": ["RECIBO TRIMESTRAL"],
        "invoice_folder": str(inv_a),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["b"] = {
        "recurrence": "quarterly",
        "patterns": ["RECIBO TRIMESTRAL"],
        "invoice_folder": str(inv_b),
        "invoice_parser": "pepeenergy",
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    assert cfg.split_groups[0].has_invoices is True


def test_split_group_with_extra_non_shared_patterns_accepted(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["a"] = {"recurrence": "quarterly", "patterns": ["SHARED", "ONLY_A"]}
    payload["categories"]["b"] = {"recurrence": "quarterly", "patterns": ["SHARED"]}
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    grp = cfg.split_groups[0]
    assert grp.patterns == ("SHARED",)
    assert tuple(m.name for m in grp.members) == ("a", "b")


def test_split_group_conflicting_peer_sets_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["a"] = {"recurrence": "quarterly", "patterns": ["P1", "P2"]}
    payload["categories"]["b"] = {"recurrence": "quarterly", "patterns": ["P1"]}
    payload["categories"]["c"] = {"recurrence": "quarterly", "patterns": ["P2"]}
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="conflicting split groups"):
        load_config(p)


def test_split_group_mismatched_recurrence_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["x"] = {"recurrence": "quarterly", "patterns": ["RECIBO TRIMESTRAL"]}
    payload["categories"]["y"] = {"recurrence": "monthly", "patterns": ["RECIBO TRIMESTRAL"]}
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="same recurrence"):
        load_config(p)


def test_split_group_recurrence_none_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["x"] = {"recurrence": "none", "patterns": ["RECIBO TRIMESTRAL"]}
    payload["categories"]["y"] = {"recurrence": "none", "patterns": ["RECIBO TRIMESTRAL"]}
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="cannot have recurrence"):
        load_config(p)


def test_split_group_mixed_invoice_folder_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    inv = tmp_path / "inv_x"
    inv.mkdir()
    payload["categories"]["x"] = {
        "recurrence": "quarterly",
        "patterns": ["RECIBO TRIMESTRAL"],
        "invoice_folder": str(inv),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["y"] = {
        "recurrence": "quarterly",
        "patterns": ["RECIBO TRIMESTRAL"],
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="invoice"):
        load_config(p)


# --- period_contains_payment flag ---


def test_period_contains_payment_accepted_with_valid_recurrence(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "yearly",
        "patterns": ["RECIBO ANUAL"],
        "period_contains_payment": True,
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert cfg.categories["fee"].period_contains_payment is True


def test_period_contains_payment_rejected_with_recurrence_none(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "none",
        "patterns": ["X"],
        "period_contains_payment": True,
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="period_contains_payment.*recurrence"):
        load_config(p)


# --- manual_mapping object form ---


def test_manual_mappings_string_form_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"X": "electricity"}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="must be an object"):
        load_config(p)


def test_manual_mapping_routes_with_category_only(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"category": "electricity"}}
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    m = cfg.manual_mappings["FOO"]
    assert m.category == "electricity"
    assert m.recurrence is None
    assert m.period_contains_payment is None


def test_manual_mapping_carries_recurrence_override(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"category": "electricity", "recurrence": "yearly"}}
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.manual_mappings["FOO"].recurrence == "yearly"


def test_manual_mapping_carries_period_contains_payment(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "FOO": {
            "category": "electricity",
            "recurrence": "yearly",
            "period_contains_payment": True,
        }
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.manual_mappings["FOO"].period_contains_payment is True


def test_manual_mapping_unknown_category_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"category": "nope"}}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="unknown category"):
        load_config(p)


def test_manual_mapping_missing_category_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"recurrence": "yearly"}}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="missing required 'category'"):
        load_config(p)


def test_manual_mapping_invalid_recurrence_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"category": "electricity", "recurrence": "weekly"}}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="invalid recurrence"):
        load_config(p)


def test_manual_mapping_pcp_must_be_bool(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "FOO": {
            "category": "electricity",
            "recurrence": "yearly",
            "period_contains_payment": "yes",
        }
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(p)


def test_manual_mapping_pcp_with_effective_none_recurrence_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["misc"] = {"recurrence": "none"}
    payload["manual_mappings"] = {"FOO": {"category": "misc", "period_contains_payment": True}}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="period_contains_payment"):
        load_config(p)
