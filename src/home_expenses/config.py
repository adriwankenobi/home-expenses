"""Load and validate config.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

Recurrence = Literal["monthly", "bimonthly", "quarterly", "yearly", "none"]
_VALID_RECURRENCES: frozenset[str] = frozenset(
    ("monthly", "bimonthly", "quarterly", "yearly", "none")
)
SplitGroupKind = Literal["all_invoiced", "none_invoiced", "mixed"]


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


# Shortest possible period, in days, for each recurrence kind. February makes
# a monthly period 28 days; Jan+Feb a bimonthly one 59; Q1 a quarterly one 90.
_MIN_PERIOD_DAYS: dict[str, int] = {
    "monthly": 28,
    "bimonthly": 59,
    "quarterly": 90,
    "yearly": 365,
}


def _parse_period_edge_days(raw: Any, recurrence: str, path: Path, name: str) -> int:
    """Validate the trailing-edge window width for a category."""
    if raw is None:
        return 0
    # bool is a subclass of int; `true` is not a day count.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(
            f"{path}: category '{name}' period_edge_days must be a non-negative "
            f"integer, got {raw!r}"
        )
    if raw < 0:
        raise ConfigError(
            f"{path}: category '{name}' period_edge_days must be a non-negative "
            f"integer, got {raw!r}"
        )
    if raw == 0:
        return 0
    if recurrence == "none":
        raise ConfigError(
            f"{path}: category '{name}' has 'period_edge_days: {raw}' but recurrence "
            f"is 'none'; this field requires a recurrence other than 'none'"
        )
    limit = _MIN_PERIOD_DAYS[recurrence]
    if raw >= limit:
        raise ConfigError(
            f"{path}: category '{name}' period_edge_days ({raw}) must be shorter "
            f"than the shortest {recurrence} period ({limit} days); a window that "
            f"wide would shift every payment"
        )
    return raw


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
    # A payment landing in the last N days of the period that contains it is
    # attributed to the FOLLOWING period — "charged a day or two early for
    # next period". 0 disables. See `assigned_period` in recurrence.py.
    period_edge_days: int = 0


@dataclass(frozen=True)
class SplitGroup:
    members: tuple[Category, ...]  # in config order
    recurrence: Recurrence  # "none" allowed only when kind == "all_invoiced"
    kind: SplitGroupKind
    patterns: tuple[str, ...]  # patterns shared across all members


@dataclass(frozen=True)
class ManualMapping:
    category: str
    amount: Decimal | None = None
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
    manual_mappings: dict[str, tuple[ManualMapping, ...]] = field(default_factory=dict)
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
    cache_dir = Path(cache_dir_raw) if cache_dir_raw else Path.cwd() / "cache"

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
        period_edge_days = _parse_period_edge_days(
            c.get("period_edge_days"), c["recurrence"], path, name
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
            period_edge_days=period_edge_days,
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
        has_inv_count = sum(1 for m in members if m.invoice_folder is not None)
        if has_inv_count == len(members):
            kind: SplitGroupKind = "all_invoiced"
        elif has_inv_count == 0:
            kind = "none_invoiced"
        else:
            kind = "mixed"
        # `none_invoiced` groups use rank-based per-period bucketing, which
        # structurally requires every member to share one recurrence
        # ("the bigger amount in each period goes to member A"). `all_invoiced`
        # routes per transaction by amount and never buckets; `mixed` routes
        # per transaction whenever group.recurrence is "none". For those two
        # kinds, mixed member recurrences are allowed — we just fall back to
        # group.recurrence = "none" so the matcher takes the per-transaction
        # path. Each member's own recurrence is preserved on the Category and
        # still drives check_recurrence and the report's display.
        if kind == "none_invoiced" and len(recs) != 1:
            raise ConfigError(
                f"{path}: none_invoiced split group {member_names} members "
                f"must all have the same recurrence (got {sorted(recs)})"
            )
        rec: Recurrence = next(iter(recs)) if len(recs) == 1 else "none"
        # Recurrence "none" is allowed for all_invoiced (amount disambiguates) and
        # for mixed (the single catch-all absorbs any non-invoice-matched txn, so
        # no period bucketing is needed). It is rejected for none_invoiced (no
        # disambiguator available).
        if rec == "none" and kind == "none_invoiced":
            raise ConfigError(
                f"{path}: split group {member_names} cannot have recurrence "
                f"'none' without invoice_folder on every member"
            )
        split_groups.append(
            SplitGroup(
                members=members,
                recurrence=rec,
                kind=kind,
                patterns=tuple(shared_patterns),
            )
        )

    mm_raw = _require_dict(raw["manual_mappings"], "manual_mappings", path)
    manual_mappings: dict[str, tuple[ManualMapping, ...]] = {}
    for desc, raw_entry in mm_raw.items():
        if isinstance(raw_entry, str):
            raise ConfigError(
                f"{path}: manual_mappings entry for '{desc}' must be an object "
                f"or a list; the legacy short string form is no longer supported"
            )
        if isinstance(raw_entry, list):
            raw_entries = raw_entry
            if len(raw_entries) == 0:
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'] must have at least one entry"
                )
        else:
            _require_dict(raw_entry, f"manual_mappings['{desc}']", path)
            raw_entries = [raw_entry]

        parsed_entries: list[ManualMapping] = []
        for i, entry in enumerate(raw_entries):
            _require_dict(entry, f"manual_mappings['{desc}'][{i}]", path)

            cat_name = entry.get("category")
            if not isinstance(cat_name, str) or cat_name == "":
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'][{i}] missing required 'category'"
                )
            if cat_name not in categories:
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'][{i}] references unknown "
                    f"category '{cat_name}'"
                )

            amount_raw = entry.get("amount")
            amount_val: Decimal | None = None
            if amount_raw is not None:
                if not isinstance(amount_raw, str):
                    raise ConfigError(
                        f"{path}: manual_mappings['{desc}'][{i}] 'amount' must be a "
                        f"decimal string (got {type(amount_raw).__name__})"
                    )
                try:
                    amount_val = Decimal(amount_raw)
                except InvalidOperation as e:
                    raise ConfigError(
                        f"{path}: manual_mappings['{desc}'][{i}] 'amount' must be a "
                        f"decimal string (got {amount_raw!r})"
                    ) from e

            recurrence_raw = entry.get("recurrence")
            if recurrence_raw is not None and recurrence_raw not in _VALID_RECURRENCES:
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'][{i}] has invalid recurrence "
                    f"'{recurrence_raw}'"
                )

            pcp = entry.get("period_contains_payment")
            if pcp is not None and not isinstance(pcp, bool):
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'][{i}] period_contains_payment "
                    f"must be a boolean"
                )

            effective_recurrence = recurrence_raw or categories[cat_name].recurrence
            if pcp is True and effective_recurrence == "none":
                raise ConfigError(
                    f"{path}: manual_mappings['{desc}'][{i}] has "
                    f"'period_contains_payment: true' but the effective recurrence is 'none'"
                )

            parsed_entries.append(
                ManualMapping(
                    category=cat_name,
                    amount=amount_val,
                    recurrence=recurrence_raw,
                    period_contains_payment=pcp,
                )
            )

        # Validate list-form invariants: at most one no-amount entry, and if
        # present it must be last (entries below it would be unreachable).
        no_amount_indices = [i for i, e in enumerate(parsed_entries) if e.amount is None]
        if len(no_amount_indices) > 1:
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] may contain at most one entry "
                f"without 'amount' (found {len(no_amount_indices)})"
            )
        if (
            len(no_amount_indices) == 1
            and no_amount_indices[0] != len(parsed_entries) - 1
        ):
            raise ConfigError(
                f"{path}: manual_mappings['{desc}'] entry without 'amount' must be "
                f"the last entry; entries after it would be unreachable"
            )

        manual_mappings[desc] = tuple(parsed_entries)

    return Config(
        currency=raw["currency"],
        bank_statements=BankStatementsConfig(dir=bs_dir, glob=bs_raw["glob"]),
        match_window_days_default=int(raw["match_window_days_default"]),
        cache_dir=cache_dir,
        categories=categories,
        manual_mappings=manual_mappings,
        split_groups=tuple(split_groups),
    )


def resolve_manual_mapping(
    description: str,
    amount: Decimal,
    config: Config,
) -> ManualMapping | None:
    """Pick the manual_mapping entry that applies to a transaction.

    Walks the tuple of entries for this description in declaration order.
    The first entry whose ``amount`` equals the transaction's amount wins.
    An entry without ``amount`` matches any amount and acts as the fallback;
    loader validation guarantees such an entry, if present, is last.
    """
    entries = config.manual_mappings.get(description)
    if entries is None:
        return None
    for entry in entries:
        if entry.amount is None:
            return entry
        if entry.amount == amount:
            return entry
    return None
