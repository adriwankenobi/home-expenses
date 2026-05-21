"""Assemble the ReportModel consumed by the renderer."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from home_expenses.models import Alert, Item, ReportCategory, ReportModel


def build_report_model(
    *,
    items: Iterable[Item],
    alerts: Iterable[Alert],
    currency: str,
    generated_at: date,
    categories_by_name: dict[str, ReportCategory],
) -> ReportModel:
    items_t = tuple(items)
    years = tuple(sorted({it.transaction.date.year for it in items_t}, reverse=True))
    return ReportModel(
        generated_at=generated_at,
        currency=currency,
        items=items_t,
        alerts=tuple(alerts),
        years=years,
        categories_by_name=dict(categories_by_name),
    )
