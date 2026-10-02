"""Movements out of the app, as a CSV or an Excel file.

The two file formats `GET /financial/export` answers with — the one part of
the financial surface that is not JSON — kept out of the router so the rules
about what a cell may contain live in one place.

Every text cell here came from a bank's email or from a person, and a
spreadsheet treats a cell that starts with `=` as a formula. The CSV is
neutralised the way OWASP describes; the workbook never writes a formula at
all.
"""

from __future__ import annotations

from collections.abc import Sequence
import csv
import datetime as dt
from decimal import Decimal
import enum
import io
from zoneinfo import ZoneInfo

import xlsxwriter  # pyright: ignore[reportMissingTypeStubs]
from xlsxwriter.format import Format  # pyright: ignore[reportMissingTypeStubs]
from xlsxwriter.worksheet import Worksheet  # pyright: ignore[reportMissingTypeStubs]

from personal_finance.contexts.financial.application.export import TransactionExport
from personal_finance.contexts.financial.application.queries import (
    AttributedTransaction,
)
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
    TransactionOrigin,
)


class ExportFormat(enum.Enum):
    CSV = "csv"
    XLSX = "xlsx"


MEDIA_TYPES = {
    ExportFormat.CSV: "text/csv; charset=utf-8",
    ExportFormat.XLSX: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
}

HEADERS = (
    "Fecha",
    "Tipo",
    "Monto",
    "Moneda",
    "Comercio",
    "Categoría",
    "Texto del banco",
    "Banco",
    "Cuenta",
    "Origen",
    "Nota",
    "ID",
)

# The shipped categories reach this module with the API's English labels; a
# file somebody opens in Spanish restates them from the value, the same table
# the frontend keeps in `@/merchants/categories`. A user's own category keeps
# the name they gave it.
CATEGORY_COPY = {
    "uncategorized": "Sin categoría",
    "groceries": "Mercado",
    "restaurants": "Restaurantes",
    "transport": "Transporte",
    "fuel": "Combustible",
    "shopping": "Compras",
    "entertainment": "Entretenimiento",
    "subscriptions": "Suscripciones",
    "utilities": "Servicios",
    "health": "Salud",
    "education": "Educación",
    "travel": "Viajes",
    "fees": "Comisiones",
    "transfers": "Transferencias",
    "income": "Ingresos",
    "other": "Otros",
}

ORIGIN_COPY = {
    TransactionOrigin.BANK_ALERT: "Correo del banco",
    TransactionOrigin.MANUAL: "Manual",
    TransactionOrigin.ACCRUAL: "Calculado por Finflow",
    TransactionOrigin.SCHEDULED: "Factura",
}

# What a spreadsheet reads as the start of a formula, including the two
# control characters some of them strip before deciding.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


# ------------------------------------------------------------------- rows

Cell = str | Decimal | dt.datetime


def export_rows(
    export: TransactionExport,
    *,
    zone: ZoneInfo,
) -> list[tuple[Cell, ...]]:
    """One tuple per movement, in `HEADERS` order.

    The date stays a naive local datetime so the workbook can store it as a
    real date — sortable and filterable — and the CSV prints it; a timezone
    offset in a cell is something no spreadsheet reads.
    """
    return [_row(entry, export=export, zone=zone) for entry in export.rows]


def _row(
    entry: AttributedTransaction,
    *,
    export: TransactionExport,
    zone: ZoneInfo,
) -> tuple[Cell, ...]:
    movement = entry.transaction
    merchant = entry.merchant
    occurred = dt.datetime.fromtimestamp(
        movement.occurred_at.as_epoch_seconds(),
        tz=zone,
    ).replace(tzinfo=None)
    account = (
        ""
        if movement.account_id is None
        else export.account_names.get(str(movement.account_id.value), "")
    )

    return (
        occurred,
        _kind(entry),
        movement.amount.amount,
        movement.amount.currency.value,
        "" if merchant is None else merchant.display_name,
        "" if merchant is None else _category(merchant.category, export),
        movement.counterparty,
        movement.bank,
        account,
        ORIGIN_COPY.get(movement.origin, movement.origin.value),
        movement.note or "",
        movement.id.value,
    )


def _kind(entry: AttributedTransaction) -> str:
    outgoing = entry.transaction.direction is MovementDirection.OUTGOING

    if entry.transaction.transfer is not None:
        # Never "Gasto": a card payment moved money between two of the
        # owner's own accounts and spent none of it.
        return "Traslado (sale)" if outgoing else "Traslado (entra)"

    return "Gasto" if outgoing else "Ingreso"


def _category(value: str, export: TransactionExport) -> str:
    return CATEGORY_COPY.get(value) or export.category_labels.get(value, value)


# --------------------------------------------------------------- encoders


def encode_csv(rows: Sequence[tuple[Cell, ...]]) -> bytes:
    """RFC 4180, comma-separated, with a byte-order mark.

    The mark is what makes Excel read the file as UTF-8 instead of turning
    every «Categoría» into mojibake; every other tool ignores it. Amounts use
    a point and no thousands separator, so any program parses them — the
    workbook is the format for somebody who wants Excel's own number display.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)

    for row in rows:
        writer.writerow([_csv_cell(cell) for cell in row])

    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _csv_cell(cell: Cell) -> str:
    if isinstance(cell, dt.datetime):
        return cell.strftime("%Y-%m-%d %H:%M")

    if isinstance(cell, Decimal):
        # Written by this module, never by a bank: a number, not text to
        # neutralise, and `format` never falls back to exponent notation.
        return format(cell, "f")

    return _neutralised(cell)


def _neutralised(text: str) -> str:
    """Text a spreadsheet will display rather than evaluate."""
    if text.startswith(_FORMULA_TRIGGERS):
        return "'" + text

    return text


def encode_xlsx(rows: Sequence[tuple[Cell, ...]]) -> bytes:
    """One sheet, a real date and a real number per row, and no formulas.

    `strings_to_formulas` off is the defence, and every text cell is still
    written with `write_string` so no option can turn one back into a formula,
    a number or a link. Amounts go in as `Decimal`, which the library prints
    digit for digit rather than through a float.
    """
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer,
        {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_numbers": False,
            "strings_to_urls": False,
        },
    )
    # XlsxWriter leaves a few parameters untyped; the values handed to them
    # here are plain literals.
    sheet = workbook.add_worksheet("Movimientos")  # pyright: ignore[reportUnknownMemberType]
    header = workbook.add_format({"bold": True})  # pyright: ignore[reportUnknownMemberType]
    date = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})  # pyright: ignore[reportUnknownMemberType]
    amount = workbook.add_format({"num_format": "#,##0.00"})  # pyright: ignore[reportUnknownMemberType]

    for column, title in enumerate(HEADERS):
        sheet.write_string(0, column, title, header)

    sheet.freeze_panes(1, 0)

    for index, row in enumerate(rows, start=1):
        for column, cell in enumerate(row):
            _xlsx_cell(sheet, index, column, cell, date=date, amount=amount)

    if rows:
        sheet.autofilter(0, 0, len(rows), len(HEADERS) - 1)

    for column, width in enumerate((17, 16, 14, 8, 24, 18, 32, 14, 20, 20, 30, 38)):
        sheet.set_column(column, column, width)

    workbook.close()

    return buffer.getvalue()


def _xlsx_cell(
    sheet: Worksheet,
    row: int,
    column: int,
    cell: Cell,
    *,
    date: Format,
    amount: Format,
) -> None:
    if isinstance(cell, dt.datetime):
        sheet.write_datetime(row, column, cell, date)
    elif isinstance(cell, Decimal):
        sheet.write_number(row, column, cell, amount)  # type: ignore[arg-type]
    else:
        sheet.write_string(row, column, cell)


def encode(file_format: ExportFormat, rows: Sequence[tuple[Cell, ...]]) -> bytes:
    if file_format is ExportFormat.XLSX:
        return encode_xlsx(rows)

    return encode_csv(rows)


def file_name(file_format: ExportFormat, *, today: dt.date) -> str:
    return f"finflow-movimientos-{today.isoformat()}.{file_format.value}"
