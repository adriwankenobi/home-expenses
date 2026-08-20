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
                "patterns": ["PEPE ENERGY INVOICE"],
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


def test_manual_mapping_key_equal_to_pattern_accepted(tmp_path: Path) -> None:
    # The previous collision check was overly strict — manual_mapping keys
    # may now equal pattern strings. The mapping is consulted by the matcher
    # in places where it's meaningful (split-group leftover routing, display
    # overrides on invoice-matched txns).
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"PEPE ENERGY INVOICE": {"category": "electricity"}}
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert "PEPE ENERGY INVOICE" in cfg.manual_mappings
    assert cfg.manual_mappings["PEPE ENERGY INVOICE"][0].category == "electricity"


def test_cache_dir_defaults_to_cwd_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write_config(tmp_path, _valid_payload(tmp_path))
    monkeypatch.chdir(tmp_path)
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
    payload: dict[str, Any], pattern: str = "QUARTERLY INVOICE"
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
    assert grp.kind == "none_invoiced"


def test_split_group_with_invoices(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    inv_b = tmp_path / "inv_b"
    inv_b.mkdir()
    payload["categories"]["a"] = {
        "recurrence": "quarterly",
        "patterns": ["QUARTERLY INVOICE"],
        "invoice_folder": str(inv_a),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["b"] = {
        "recurrence": "quarterly",
        "patterns": ["QUARTERLY INVOICE"],
        "invoice_folder": str(inv_b),
        "invoice_parser": "pepeenergy",
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    assert cfg.split_groups[0].kind == "all_invoiced"


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


def test_split_group_mismatched_recurrence_rejected_when_none_invoiced(
    tmp_path: Path,
) -> None:
    # `none_invoiced` groups rank-assign by amount per period bucket, which
    # requires a single shared period definition — mixed recurrences are
    # rejected only for this kind.
    payload = _valid_payload(tmp_path)
    payload["categories"]["x"] = {"recurrence": "quarterly", "patterns": ["QUARTERLY INVOICE"]}
    payload["categories"]["y"] = {"recurrence": "monthly", "patterns": ["QUARTERLY INVOICE"]}
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="same recurrence"):
        load_config(p)


def test_split_group_mismatched_recurrence_accepted_when_all_invoiced(
    tmp_path: Path,
) -> None:
    # `all_invoiced` groups never bucket — they route per transaction by
    # amount. Mixed member recurrences are allowed; group.recurrence falls
    # back to "none" so the matcher uses the per-transaction path.
    payload = _valid_payload(tmp_path)
    inv_x = tmp_path / "inv_x"
    inv_x.mkdir()
    inv_y = tmp_path / "inv_y"
    inv_y.mkdir()
    payload["categories"]["x"] = {
        "recurrence": "quarterly",
        "patterns": ["QUARTERLY INVOICE"],
        "invoice_folder": str(inv_x),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["y"] = {
        "recurrence": "monthly",
        "patterns": ["QUARTERLY INVOICE"],
        "invoice_folder": str(inv_y),
        "invoice_parser": "pepeenergy",
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.kind == "all_invoiced"
    assert sg.recurrence == "none"
    # Member recurrences are preserved on the Category itself.
    assert cfg.categories["x"].recurrence == "quarterly"
    assert cfg.categories["y"].recurrence == "monthly"


def test_split_group_mismatched_recurrence_accepted_when_mixed(
    tmp_path: Path,
) -> None:
    # `mixed` groups route per transaction whenever group.recurrence == "none".
    # When members have non-uniform recurrences, group.recurrence falls back
    # to "none" so the per-transaction path is used. Each member's own
    # recurrence drives check_recurrence and the report's display.
    payload = _valid_payload(tmp_path)
    inv_x = tmp_path / "inv_x"
    inv_x.mkdir()
    payload["categories"]["x"] = {
        "recurrence": "none",
        "patterns": ["SHARED"],
        "invoice_folder": str(inv_x),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["catchall"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED"],
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.kind == "mixed"
    assert sg.recurrence == "none"
    assert cfg.categories["x"].recurrence == "none"
    assert cfg.categories["catchall"].recurrence == "monthly"


def test_split_group_recurrence_none_rejected_without_invoices(tmp_path: Path) -> None:
    # Recurrence "none" only works when both members have invoice folders —
    # the matcher routes via amount, which doesn't need period buckets. Without
    # invoice folders the rank-based fallback path would fire and there's no
    # meaningful period to bucket into.
    payload = _valid_payload(tmp_path)
    payload["categories"]["x"] = {"recurrence": "none", "patterns": ["QUARTERLY INVOICE"]}
    payload["categories"]["y"] = {"recurrence": "none", "patterns": ["QUARTERLY INVOICE"]}
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="cannot have recurrence"):
        load_config(p)


def test_split_group_recurrence_none_allowed_with_invoices(tmp_path: Path) -> None:
    # Two one-off categories sharing a transaction description but with their
    # own invoices: amount disambiguates which category each transaction
    # belongs to, so the rank-fallback path is never needed.
    payload = _valid_payload(tmp_path)
    inv_x = tmp_path / "inv_x"
    inv_x.mkdir()
    inv_y = tmp_path / "inv_y"
    inv_y.mkdir()
    payload["categories"]["x"] = {
        "recurrence": "none",
        "patterns": ["ONEOFF INVOICE"],
        "invoice_folder": str(inv_x),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["y"] = {
        "recurrence": "none",
        "patterns": ["ONEOFF INVOICE"],
        "invoice_folder": str(inv_y),
        "invoice_parser": "pepeenergy",
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.recurrence == "none"
    assert sg.kind == "all_invoiced"
    assert {m.name for m in sg.members} == {"x", "y"}


def test_split_group_mixed_invoices_accepted(tmp_path: Path) -> None:
    # A split group with one invoiced member and one non-invoiced (catch-all)
    # member is accepted. The matcher routes invoice-matched transactions to
    # the invoiced member and any leftover to the catch-all.
    payload = _valid_payload(tmp_path)
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    payload["categories"]["a"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED PATTERN"],
        "invoice_folder": str(inv_a),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["catchall"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED PATTERN"],
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.kind == "mixed"
    assert {m.name for m in sg.members} == {"a", "catchall"}


def test_split_group_mixed_with_recurrence_none_accepted(tmp_path: Path) -> None:
    # Mixed groups (1 invoiced + 1 noninv catch-all) accept recurrence "none"
    # because the catch-all absorbs any non-invoice-matched transaction; no
    # period bucketing is needed for the single-catch-all case.
    payload = _valid_payload(tmp_path)
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    payload["categories"]["a"] = {
        "recurrence": "none",
        "patterns": ["ONEOFF"],
        "invoice_folder": str(inv_a),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["catchall"] = {
        "recurrence": "none",
        "patterns": ["ONEOFF"],
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.kind == "mixed"
    assert sg.recurrence == "none"


def test_split_group_mixed_with_two_noninv_accepted(tmp_path: Path) -> None:
    # Mixed groups may have any number of non-invoiced members. Leftover
    # routing is delegated to manual_mappings (amount-based), so the load
    # rule that previously restricted to exactly one non-invoiced member is
    # gone. The matcher still requires every leftover to resolve via
    # manual_mappings — that's a runtime alert, not a config error.
    payload = _valid_payload(tmp_path)
    inv_a = tmp_path / "inv_a"
    inv_a.mkdir()
    payload["categories"]["a"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED"],
        "invoice_folder": str(inv_a),
        "invoice_parser": "pepeenergy",
    }
    payload["categories"]["b"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED"],
    }
    payload["categories"]["c"] = {
        "recurrence": "monthly",
        "patterns": ["SHARED"],
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert len(cfg.split_groups) == 1
    sg = cfg.split_groups[0]
    assert sg.kind == "mixed"
    assert {m.name for m in sg.members} == {"a", "b", "c"}


# --- period_contains_payment flag ---


def test_period_contains_payment_accepted_with_valid_recurrence(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "yearly",
        "patterns": ["ANNUAL INVOICE"],
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


# --- period_edge_days ---


def test_period_edge_days_defaults_to_zero(tmp_path: Path) -> None:
    p = _write_config(tmp_path, _valid_payload(tmp_path))

    cfg = load_config(p)

    assert cfg.categories["electricity"].period_edge_days == 0


def test_period_edge_days_accepted(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "monthly",
        "patterns": ["FEE"],
        "period_edge_days": 2,
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert cfg.categories["fee"].period_edge_days == 2


def test_period_edge_days_rejected_with_recurrence_none(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "none",
        "patterns": ["FEE"],
        "period_edge_days": 2,
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="period_edge_days.*recurrence"):
        load_config(p)


def test_period_edge_days_rejected_when_negative(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "monthly",
        "patterns": ["FEE"],
        "period_edge_days": -1,
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="period_edge_days"):
        load_config(p)


def test_period_edge_days_rejected_when_not_an_integer(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "monthly",
        "patterns": ["FEE"],
        "period_edge_days": "2",
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="period_edge_days"):
        load_config(p)


def test_period_edge_days_rejected_when_as_long_as_the_period(tmp_path: Path) -> None:
    # A monthly period can be 28 days; an edge window that long would swallow
    # the whole period and shift every charge.
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "monthly",
        "patterns": ["FEE"],
        "period_edge_days": 28,
    }
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError, match="period_edge_days"):
        load_config(p)


def test_period_edge_days_large_value_allowed_for_yearly(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["categories"]["fee"] = {
        "recurrence": "yearly",
        "patterns": ["FEE"],
        "period_edge_days": 20,
    }
    p = _write_config(tmp_path, payload)

    cfg = load_config(p)

    assert cfg.categories["fee"].period_edge_days == 20


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
    entries = cfg.manual_mappings["FOO"]
    assert len(entries) == 1
    m = entries[0]
    assert m.category == "electricity"
    assert m.recurrence is None
    assert m.period_contains_payment is None


def test_manual_mapping_carries_recurrence_override(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"FOO": {"category": "electricity", "recurrence": "yearly"}}
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    assert cfg.manual_mappings["FOO"][0].recurrence == "yearly"


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
    assert cfg.manual_mappings["FOO"][0].period_contains_payment is True


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


def test_resolve_manual_mapping_returns_entry_for_known_description(
    tmp_path: Path,
) -> None:
    from decimal import Decimal as _Decimal

    from home_expenses.config import resolve_manual_mapping

    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "UNIQUE ANNUAL CHARGE": {"category": "electricity"},
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)

    mapping = resolve_manual_mapping("UNIQUE ANNUAL CHARGE", _Decimal("100.00"), cfg)
    assert mapping is not None
    assert mapping.category == "electricity"
    # No amount filter on the entry, so any amount resolves to it.
    assert mapping.amount is None


def test_resolve_manual_mapping_returns_none_for_unknown_description(
    tmp_path: Path,
) -> None:
    from decimal import Decimal as _Decimal

    from home_expenses.config import resolve_manual_mapping

    payload = _valid_payload(tmp_path)
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)

    assert resolve_manual_mapping("NOT IN CONFIG", _Decimal("5.00"), cfg) is None


def test_manual_mapping_list_form_parses(tmp_path: Path) -> None:
    from decimal import Decimal as _Decimal

    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [
            {"category": "electricity", "amount": "75.00"},
            {"category": "electricity", "amount": "120.00"},
            {"category": "electricity"},
        ],
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)
    entries = cfg.manual_mappings["SHARED DESC"]
    assert len(entries) == 3
    assert entries[0].amount == _Decimal("75.00")
    assert entries[1].amount == _Decimal("120.00")
    assert entries[2].amount is None


def test_manual_mapping_empty_list_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {"SHARED DESC": []}
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="at least one entry"):
        load_config(p)


def test_manual_mapping_two_no_amount_entries_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [
            {"category": "electricity"},
            {"category": "electricity"},
        ],
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="at most one entry without 'amount'"):
        load_config(p)


def test_manual_mapping_no_amount_entry_not_last_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [
            {"category": "electricity"},
            {"category": "electricity", "amount": "75.00"},
        ],
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="must be the last entry"):
        load_config(p)


def test_manual_mapping_amount_not_decimal_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [{"category": "electricity", "amount": "not-a-number"}],
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="must be a decimal string"):
        load_config(p)


def test_manual_mapping_entry_with_unknown_category_rejected(tmp_path: Path) -> None:
    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [{"category": "does-not-exist", "amount": "10.00"}],
    }
    p = _write_config(tmp_path, payload)
    with pytest.raises(ConfigError, match="unknown category"):
        load_config(p)


def test_resolve_manual_mapping_first_amount_match_wins(tmp_path: Path) -> None:
    from decimal import Decimal as _Decimal

    from home_expenses.config import resolve_manual_mapping

    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [
            {"category": "electricity", "amount": "75.00"},
            {"category": "electricity", "amount": "120.00"},
        ],
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)

    m = resolve_manual_mapping("SHARED DESC", _Decimal("75.00"), cfg)
    assert m is not None and m.amount == _Decimal("75.00")

    m = resolve_manual_mapping("SHARED DESC", _Decimal("120.00"), cfg)
    assert m is not None and m.amount == _Decimal("120.00")

    m = resolve_manual_mapping("SHARED DESC", _Decimal("999.00"), cfg)
    assert m is None


def test_resolve_manual_mapping_fallback_catches_non_match(tmp_path: Path) -> None:
    from decimal import Decimal as _Decimal

    from home_expenses.config import resolve_manual_mapping

    payload = _valid_payload(tmp_path)
    payload["manual_mappings"] = {
        "SHARED DESC": [
            {"category": "electricity", "amount": "75.00"},
            {"category": "electricity"},
        ],
    }
    p = _write_config(tmp_path, payload)
    cfg = load_config(p)

    m = resolve_manual_mapping("SHARED DESC", _Decimal("75.00"), cfg)
    assert m is not None and m.amount == _Decimal("75.00")

    m = resolve_manual_mapping("SHARED DESC", _Decimal("999.00"), cfg)
    assert m is not None and m.amount is None
