"""Single source of truth for money arithmetic.

Everything that touches currency amounts (invoice generation, the validation
engine, extraction post-processing) must go through here. No floats: a float
subtotal computed one way and a Decimal subtotal computed another way can
differ by a cent, which is exactly the kind of false TOTAL_MISMATCH this
project exists to avoid triggering on clean invoices.

Rounding convention: ROUND_HALF_UP to 2dp, applied once per computed amount
(line amount, subtotal, GST, total) rather than re-rounding intermediate
sums. This matches how a cashier/accounting system rounds a line amount
once it is computed, not how a scientific calculation would propagate
precision. See docs/data_sources.md for the GST-rounding citation.
"""
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


def D(value) -> Decimal:
    """Convert a number or string to Decimal without a float round-trip.

    Decimal(0.1) != Decimal("0.1") -- floats can't represent most decimal
    fractions exactly, so always go through str() first unless the input is
    already an int/str/Decimal.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def round2(value) -> Decimal:
    return D(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money_str(value) -> str:
    """Canonical JSON representation for a money amount: a 2dp string."""
    return str(round2(value))
