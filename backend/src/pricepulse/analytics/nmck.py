"""НМЦК (Начальная Максимальная Цена Контракта) calculator + Excel
export — Приложение №1 to 44-ФЗ, метод сопоставимых рыночных цен.

Statistics follow the standard MED-РФ guidance:
  Средняя арифметическая  μ = Σx / n
  Среднеквадратическое отклонение  σ = √(Σ(x − μ)² / (n − 1))
  Коэффициент вариации  V = σ / μ × 100 %

V ≤ 33 %  → выборка однородна, НМЦК = μ
V > 33 %  → выборка неоднородна, нужно расширить (4+ КП) или применить
            метод тарифа / проектно-сметный — мы помечаем это в отчёте.

The Excel layout intentionally mimics the spreadsheets госзакупщики
already paste into тендерную документацию: header block, three-column
КП table, computed statistics, final НМЦК cell — all formulas live in
the workbook so the user can edit quantities and watch totals update.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from pricepulse.domain.models import ProductOffer

# ─────────────────────────────────────────── pure-Python core ─────────


@dataclass(frozen=True)
class NmckStats:
    n_offers: int
    mean: Decimal
    stdev: Decimal
    cv_pct: float                 # коэф. вариации в %
    homogeneous: bool             # True if V <= 33 %
    nmck_per_unit: Decimal        # = mean
    sources_used: list[str]       # human-readable seller list


def compute(offers: list[ProductOffer]) -> NmckStats | None:
    """Pick the cheapest offer from each unique seller (or source), trim
    to at most 5 КП, compute statistics. Returns None when there are
    fewer than three КП — 44-ФЗ requires three minimum."""
    if not offers:
        return None
    # One КП per seller — keep the cheapest. Falls back to source if
    # seller is missing (Yandex SERP path doesn't always have it).
    by_seller: dict[str, ProductOffer] = {}
    for o in offers:
        if o.price is None or o.price <= 0:
            continue
        key = (o.seller or o.source.value).strip().lower()
        cur = by_seller.get(key)
        if cur is None or o.price < cur.price:
            by_seller[key] = o
    chosen = sorted(by_seller.values(), key=lambda o: o.price)[:5]
    if len(chosen) < 3:
        return None
    prices = [float(o.price) for o in chosen]
    mu = sum(prices) / len(prices)
    var = sum((p - mu) ** 2 for p in prices) / (len(prices) - 1)
    sigma = math.sqrt(var)
    v = (sigma / mu * 100) if mu > 0 else 0.0
    return NmckStats(
        n_offers=len(chosen),
        mean=Decimal(str(round(mu, 2))),
        stdev=Decimal(str(round(sigma, 2))),
        cv_pct=round(v, 2),
        homogeneous=v <= 33.0,
        nmck_per_unit=Decimal(str(round(mu, 2))),
        sources_used=[(o.seller or o.source.value) for o in chosen],
    )


# ─────────────────────────────────────────── Excel renderer ──────────


_BORDER = Border(
    left=Side(border_style="thin"),
    right=Side(border_style="thin"),
    top=Side(border_style="thin"),
    bottom=Side(border_style="thin"),
)
_HEADER_FILL = PatternFill("solid", fgColor="DDE9F7")
_TOTAL_FILL = PatternFill("solid", fgColor="FFF2CC")


def to_excel(
    *,
    query: str,
    offers: list[ProductOffer],
    stats: NmckStats,
    quantity: int = 1,
) -> bytes:
    """Render Приложение №1 — обоснование НМЦК. Returns xlsx bytes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Обоснование НМЦК"

    # Column widths — fit-to-content with bias for the seller column
    widths = [4, 38, 22, 18, 18, 38]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    def _put(row: int, col: int, value, *, bold=False, fill=None,
             border=False, align=None, num_format=None) -> None:
        cell = ws.cell(row=row, column=col, value=value)
        if bold:
            cell.font = Font(bold=True)
        if fill is not None:
            cell.fill = fill
        if border:
            cell.border = _BORDER
        if align is not None:
            cell.alignment = align
        if num_format is not None:
            cell.number_format = num_format

    centred = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right = Alignment(horizontal="right", vertical="center")

    # Header
    ws.merge_cells("A1:F1")
    _put(1, 1, "Приложение № 1", bold=True, align=Alignment(horizontal="right"))
    ws.merge_cells("A2:F2")
    _put(2, 1, "к обоснованию НМЦК методом сопоставимых рыночных цен (анализа рынка)",
         align=Alignment(horizontal="right", wrap_text=True))

    ws.merge_cells("A4:F4")
    _put(4, 1, "ОБОСНОВАНИЕ НАЧАЛЬНОЙ (МАКСИМАЛЬНОЙ) ЦЕНЫ КОНТРАКТА",
         bold=True, align=centred)
    ws.row_dimensions[4].height = 28

    today = datetime.now(tz=UTC).strftime("%d.%m.%Y")
    _put(6, 1, "Предмет закупки:", bold=True)
    ws.merge_cells("B6:F6")
    _put(6, 2, query, align=left_wrap)
    _put(7, 1, "Дата исследования:", bold=True)
    _put(7, 2, today)
    _put(8, 1, "Метод обоснования:", bold=True)
    ws.merge_cells("B8:F8")
    _put(8, 2, "Метод сопоставимых рыночных цен (анализа рынка) — ч.6 ст.22 44-ФЗ",
         align=left_wrap)

    # КП table header
    hdr_row = 11
    headers = ["№", "Источник (поставщик)", "Цена за ед., ₽", "Кол-во",
               "Стоимость, ₽", "Ссылка / комментарий"]
    for col, h in enumerate(headers, start=1):
        _put(hdr_row, col, h, bold=True, fill=_HEADER_FILL,
             border=True, align=centred)
    ws.row_dimensions[hdr_row].height = 28

    # КП rows — one per cheapest-per-seller offer, capped at 5
    by_seller: dict[str, ProductOffer] = {}
    for o in offers:
        if o.price is None or o.price <= 0:
            continue
        key = (o.seller or o.source.value).strip().lower()
        cur = by_seller.get(key)
        if cur is None or o.price < cur.price:
            by_seller[key] = o
    chosen = sorted(by_seller.values(), key=lambda o: o.price)[:5]

    first_data = hdr_row + 1
    for i, o in enumerate(chosen, start=1):
        r = first_data + i - 1
        _put(r, 1, i, border=True, align=centred)
        _put(r, 2, o.seller or o.source.value, border=True, align=left_wrap)
        _put(r, 3, float(o.price), border=True, align=right,
             num_format="#,##0.00 ₽")
        _put(r, 4, quantity, border=True, align=right)
        # Stoimost' = price * qty — формула, чтобы редактировать в Excel
        _put(r, 5, f"=C{r}*D{r}", border=True, align=right,
             num_format="#,##0.00 ₽")
        _put(r, 6, o.name, border=True, align=left_wrap)

    last_data = first_data + len(chosen) - 1

    # Statistics block
    stats_row = last_data + 2
    _put(stats_row,     1, "Средняя цена за единицу, μ:", bold=True)
    _put(stats_row,     5, f"=AVERAGE(C{first_data}:C{last_data})",
         bold=True, border=True, align=right, num_format="#,##0.00 ₽")
    _put(stats_row + 1, 1, "Среднеквадратическое отклонение, σ:", bold=True)
    _put(stats_row + 1, 5, f"=STDEV(C{first_data}:C{last_data})",
         border=True, align=right, num_format="#,##0.00 ₽")
    _put(stats_row + 2, 1, "Коэффициент вариации, V:", bold=True)
    _put(stats_row + 2, 5, f"=E{stats_row + 1}/E{stats_row}*100",
         border=True, align=right, num_format="0.00\\%")
    _put(stats_row + 3, 1, "Однородность выборки (V ≤ 33%):", bold=True)
    _put(stats_row + 3, 5,
         "ОДНОРОДНА" if stats.homogeneous else "НЕОДНОРОДНА, нужно расширить выборку",
         border=True, align=centred)

    # NMCK total
    final_row = stats_row + 5
    ws.merge_cells(f"A{final_row}:D{final_row}")
    _put(final_row, 1, "НМЦК (рекомендуемая), ₽:", bold=True, fill=_TOTAL_FILL,
         align=Alignment(horizontal="right", vertical="center"))
    _put(final_row, 5, f"=E{stats_row}*{quantity}",
         bold=True, border=True, fill=_TOTAL_FILL, align=right,
         num_format="#,##0.00 ₽")

    # Footer note
    note_row = final_row + 2
    ws.merge_cells(f"A{note_row}:F{note_row + 2}")
    note = (
        "Источник данных: автоматический агрегатор PricePulse "
        f"(WB / Ozon / Рунет), {today}. Количество поставщиков в выборке: "
        f"{stats.n_offers} ≥ 3 (требование ч.5 ст.22 44-ФЗ). "
        "Формулы в ячейках сохраняют расчёт при изменении количества."
    )
    _put(note_row, 1, note, align=left_wrap)
    ws.row_dimensions[note_row].height = 56

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


__all__ = ["NmckStats", "compute", "to_excel"]
