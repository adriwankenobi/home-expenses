from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from home_expenses.cli import cli


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """Build a synthetic project tree and return (config_path, output_path)."""
    statements_dir = tmp_path / "bank"
    statements_dir.mkdir()
    invoices_dir = tmp_path / "pepe"
    invoices_dir.mkdir()
    templates_dir = Path(__file__).resolve().parent / "templates"
    shutil.copy(templates_dir / "bank_statement.csv", statements_dir)

    cfg_payload = {
        "currency": "EUR",
        "bank_statements": {"dir": str(statements_dir), "glob": "*.csv"},
        "match_window_days_default": 30,
        "categories": {
            "electricity": {
                "invoice_folder": str(invoices_dir),
                "invoice_parser": "pepeenergy",
                "recurrence": "monthly",
                "patterns": ["PEPE ENERGY INVOICE"],
            },
            "bank_fees": {
                "patterns": ["ACCOUNT MAINTENANCE FEE"],
                "recurrence": "yearly",
            },
        },
        "manual_mappings": {},
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(cfg_payload), encoding="utf-8")
    output = tmp_path / "report.html"
    return cfg, output


def test_report_command_writes_html(tmp_path: Path) -> None:
    cfg, output = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["report", "--config", str(cfg), "--output", str(output), "--no-open"],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()
    html = output.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE")
    # Console output is operational only (no amounts).
    assert "11.11" not in result.output
    assert "11,11" not in result.output


def test_report_aborts_on_invalid_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--config", str(cfg)])
    assert result.exit_code != 0


def test_cache_clear_command(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "pdf-extractions.json").write_text("{}", encoding="utf-8")
    (cache_dir / "statements.json").write_text("{}", encoding="utf-8")

    # Build a minimal config pointing cache_dir at our temp dir.
    statements_dir = tmp_path / "bank"
    statements_dir.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "currency": "EUR",
                "bank_statements": {"dir": str(statements_dir), "glob": "*.csv"},
                "match_window_days_default": 30,
                "cache_dir": str(cache_dir),
                "categories": {},
                "manual_mappings": {},
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear", "--config", str(cfg)])
    assert result.exit_code == 0
    assert not (cache_dir / "pdf-extractions.json").exists()
