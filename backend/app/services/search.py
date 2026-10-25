from sqlalchemy import ColumnElement
from sqlalchemy.orm import InstrumentedAttribute

LIKE_ESCAPE = "\\"


def name_contains(column: InstrumentedAttribute[str], text: str) -> ColumnElement[bool]:
    """Case-insensitive substring match in which `%`, `_` and `\\` in the input match
    literally instead of acting as wildcards."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape=LIKE_ESCAPE)
