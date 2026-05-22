"""Load and validate config.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

Recurrence = Literal["monthly", "bimonthly", "quarterly", "yearly", "none"]
_VALID_RECURRENCES: frozenset[str] = frozenset(
    ("monthly", "bimonthly", "quarterly", "yearly", "none")
)


class ConfigError(ValueError):
    """Raised when config.json is structurally invalid."""


def _require_dict(value: Any, label: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: {label} must be an object")
    return value


_BIMONTHLY_FIRST_MONTHS: frozenset[int] = frozenset((1, 3, 5, 7, 9, 11))
_QUARTERLY_RE = re.compile(r"^(\d{4})-Q([1-4])$")


def _parse_start_date(raw: str, recurrence: str, path: Path, name: str) -> date:
    """Parse start_date in the granularity that matches the recurrence kind."""
    if recurrence == "monthly":
        try:
            return datetime.strptime(raw, "%Y-%m").date()
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"{path}: category '{name}' start_date for monthly recurrence "
                f"must be YYYY-MM, got {raw!r}"
            ) from e
    if recurrence == "bimonthly":
        try:
            d = datetime.strptime(raw, "%Y-%m").date()
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"{path}: category '{name}' start_date for bimonthly recurrence "
                f"must be YYYY-MM (first month of pair), got {raw!r}"
            ) from e
        if d.month not in _BIMONTHLY_FIRST_MONTHS:
            raise ConfigError(
                f"{path}: category '{name}' bimonthly start_date month must be "
                f"one of Jan/Mar/May/Jul/Sep/Nov, got {raw!r}"
            )
        return d
    if recurrence == "quarterly":
        m = _QUARTERLY_RE.match(raw) if isinstance(raw, str) else None
        if m is None:
            raise ConfigError(
                f"{path}: category '{name}' start_date for quarterly recurrence "
                f"must be YYYY-Q1..YYYY-Q4, got {raw!r}"
            )
        year = int(m.group(1))
        quarter = int(m.group(2))
        return date(year, (quarter - 1) * 3 + 1, 1)
    if recurrence == "yearly":
        if not isinstance(raw, str) or not raw.isdigit() or len(raw) != 4:
            raise ConfigError(
                f"{path}: category '{name}' start_date for yearly recurrence "
                f"must be YYYY, got {raw!r}"
            )
        return date(int(raw), 1, 1)
    # recurrence == "none": day-precision date
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError) as e:
        raise ConfigError(
            f"{path}: category '{name}' start_date for non-recurring category "
            f"must be YYYY-MM-DD, got {raw!r}"
        ) from e


@dataclass(frozen=True)
class Category:
    name: str
    recurrence: Recurrence
    invoice_folder: Path | None = None
    invoice_parser: str | None = None
    patterns: tuple[str, ...] = ()
    match_window_days: int | None = None
    start_date: date | None = None  # No "missing" alerts before this date.
    period_contains_payment: bool = False


@dataclass(frozen=True)
class SplitGroup:
    members: tuple[Category, ...]  # in config order
    recurrence: Recurrence  # "none" allowed only when has_invoices is True
    has_invoices: bool
    patterns: tuple[str, ...]  # patterns shared across all members


@dataclass(frozen=True)
class ManualMapping:
    category: str
    recurrence: Recurrence | None = None
    period_contains_payment: bool | None = None


@dataclass(frozen=True)
class BankStatementsConfig:
    dir: Path
    glob: str


@dataclass(frozen=True)
class Config:
    currency: str
    bank_statements: BankStatementsConfig
    match_window_days_default: int
    cache_dir: Path
    categories: dict[str, Category] = field(default_factory=dict)
    manual_mappings: dict[str, ManualMapping] = field(default_factory=dict)
    split_groups: tuple[SplitGroup, ...] = ()


def load_config(path: Path) -> Config:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON: {e}") from e

    for required in (
        "currency",
        "bank_statements",
        "match_window_days_default",
        "categories",
        "manual_mappings",
    ):
        if required not in raw:
            raise ConfigError(f"{path}: missing required field '{required}'")

    bs_raw: dict[str, Any] = _require_dict(raw["bank_statements"], "bank_statements", path)
    if "dir" not in bs_raw or "glob" not in bs_raw:
        raise ConfigError(f"{path}: bank_statements.dir and .glob are required")
    bs_dir = Path(bs_raw["dir"])
    if not bs_dir.is_dir():
        raise ConfigError(f"{path}: bank_statements.dir does not exist: {bs_dir}")

    cache_dir_raw = raw.get("cache_dir")
    cache_dir = Path(cache_dir_raw) if cache_dir_raw else path.parent / "cache"

    cats_raw = raw["categories"]
    if isinstance(cats_raw, list):
        raise ConfigError(f"{path}: categories must be a JSON object")
    _require_dict(cats_raw, "categories", path)

    categories: dict[str, Category] = {}
    pattern_owners: dict[str, list[str]] = {}
    for name, c in cats_raw.items():
        _require_dict(c, f"category '{name}'", path)
        if "recurrence" not in c:
            raise ConfigError(f"{path}: category '{name}' missing 'recurrence'")
        if c["recurrence"] not in _VALID_RECURRENCES:
            raise ConfigError(
                f"{path}: category '{name}' has invalid recurrence '{c['recurrence']}'"
            )
        invoice_folder_raw = c.get("invoice_folder")
        invoice_folder: Path | None = None
        if invoice_folder_raw is not None:
            invoice_folder = Path(invoice_folder_raw)
            if not invoice_folder.is_dir():
                raise ConfigError(
                    f"{path}: category '{name}' invoice_folder does not exist: {invoice_folder}"
                )
            if "invoice_parser" not in c:
                raise ConfigError(
                    f"{path}: category '{name}' has invoice_folder but no invoice_parser"
                )
        patterns = tuple(c.get("patterns", ()))
        for p in patterns:
            pattern_owners.setdefault(p, []).append(name)
        start_date_raw = c.get("start_date")
        start_date_val: date | None = None
        if start_date_raw is not None:
            start_date_val = _parse_start_date(start_date_raw, c["recurrence"], path, name)
        period_contains_payment = bool(c.get("period_contains_payment", False))
        if period_contains_payment and c["recurrence"] == "none":
            raise ConfigError(
                f"{path}: category '{name}' has 'period_contains_payment: true' but "
                f"recurrence is 'none'; this flag requires a recurrence other than 'none'"
            )
        categories[name] = Category(
            name=name,
            recurrence=c["recurrence"],
            invoice_folder=invoice_folder,
            invoice_parser=c.get("invoice_parser"),
            patterns=patterns,
            match_window_days=c.get("match_window_days"),
            start_date=start_date_val,
            period_contains_payment=period_contains_payment,
        )

    # Build implicit split groups from shared patterns. A category may have
    # extra non-shared patterns that route normally; only patterns owned by 2+
    # categories trigger split-group behavior. A category that participates in
    # split groups must do so via exactly one peer set — sharing different
    # patterns with different peers is rejected.
    owner_peer_set: dict[str, frozenset[str]] = {}
    patterns_by_peer_set: dict[frozenset[str], list[str]] = {}
    for pat, owners in pattern_owners.items():
        if len(owners) < 2:
            continue
        peer_set = frozenset(owners)
        for o in owners:
            existing = owner_peer_set.get(o)
            if existing is not None and existing != peer_set:
                raise ConfigError(
                    f"{path}: category '{o}' participates in conflicting "
                    f"split groups: shared with {sorted(existing - {o})} "
                    f"and with {sorted(peer_set - {o})}"
                )
            owner_peer_set[o] = peer_set
        patterns_by_peer_set.setdefault(peer_set, []).append(pat)

    split_groups: list[SplitGroup] = []
    for peer_set, shared_patterns in patterns_by_peer_set.items():
        # Iterate categories in config (insertion) order, filter by peer set.
        members = tuple(categories[n] for n in categories if n in peer_set)
        member_names = [m.name for m in members]
        recs = {m.recurrence for m in members}
        if len(recs) != 1:
            raise ConfigError(
                f"{path}: split group {member_names} members must all "
                f"have the same recurrence (got {sorted(recs)})"
            )
        (rec,) = recs
        has_inv = {m.invoice_folder is not None for m in members}
        if len(has_inv) != 1:
            raise ConfigError(
                f"{path}: split group {member_names} must either all "
                f"have invoice_folder set or none of them"
            )
        has_invoices = has_inv.pop()
        # Recurrence "none" is only viable when every member has an invoice
        # folder: amount alone disambiguates which category each transaction
        # belongs to, so the period-based rank-fallback path never fires.
        # Without invoices the matcher needs a real recurrence to bucket by.
        if rec == "none" and not has_invoices:
            raise ConfigError(
                f"{path}: split group {member_names} cannot have recurrence "
                f"'none' without invoice_folder on every member"
            )
        split_groups.append(
            SplitGroup(
                members=members,
                recurrence=rec,
                has_invoices=has_invoices,
                patterns=tuple(shared_patterns),
            )
        )

    mm_raw = _require_dict(raw["manual_mappings"], "manual_mappings", path)
    manual_mappings: dict[str, ManualMapping] = {}
    for desc, entry in mm_raw.items():
        if isinstance(entry, str):
            raise ConfigError(
                f"{path}: manual_mappings entry for '{desc}' must be an object "
                f"with at least a 'category' field; the legacy short string "
                f"form is no longer supported"
            )
        _require_dict(entry, f"manual_mappings['{desc}']", path)

        cat_name = entry.get("category")
        if not isinstance(cat_name, str) or cat_name == "":
            raise ConfigError(f"{path}: manual_mappings['{desc}'] missing required 'category'")
        if cat_name not in categories:
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] references unknown category '{cat_name}'"
            )

        recurrence_raw = entry.get("recurrence")
        if recurrence_raw is not None and recurrence_raw not in _VALID_RECURRENCES:
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] has invalid recurrence '{recurrence_raw}'"
            )

        pcp = entry.get("period_contains_payment")
        if pcp is not None and not isinstance(pcp, bool):
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] period_contains_payment must be a boolean"
            )

        effective_recurrence = recurrence_raw or categories[cat_name].recurrence
        if pcp is True and effective_recurrence == "none":
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] has 'period_contains_payment: true' "
                f"but the effective recurrence is 'none'"
            )

        manual_mappings[desc] = ManualMapping(
            category=cat_name,
            recurrence=recurrence_raw,
            period_contains_payment=pcp,
        )

    for desc in manual_mappings:
        if desc in pattern_owners:
            raise ConfigError(
                f"{path}: manual_mapping key '{desc}' collides with pattern "
                f"in category '{pattern_owners[desc][0]}'"
            )

    return Config(
        currency=raw["currency"],
        bank_statements=BankStatementsConfig(dir=bs_dir, glob=bs_raw["glob"]),
        match_window_days_default=int(raw["match_window_days_default"]),
        cache_dir=cache_dir,
        categories=categories,
        manual_mappings=manual_mappings,
        split_groups=tuple(split_groups),
    )
