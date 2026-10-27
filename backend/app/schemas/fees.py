from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

MAX_AMOUNT = Decimal("9999999999.99")  # the NUMERIC(12,2) column limit


def _not_a_boolean(value: Any) -> Any:
    if isinstance(value, bool):
        # ValueError, not TypeError: only ValueError becomes a 422.
        raise ValueError("Input should be a valid number")  # noqa: TRY004
    return value


# A naira amount from the client: a JSON number or string, greater than zero, at most
# two decimals. NaN, infinity and booleans are refused, and nothing is rounded or
# clamped. Responses carry amounts as strings with two decimals, e.g. "25000.00"; the
# services quantize them (`money.quantize`) before building a response.
Amount = Annotated[
    Decimal,
    BeforeValidator(_not_a_boolean),
    Field(gt=0, le=MAX_AMOUNT, max_digits=12, decimal_places=2, allow_inf_nan=False),
]
