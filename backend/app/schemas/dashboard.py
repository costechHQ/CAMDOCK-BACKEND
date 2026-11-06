from pydantic import BaseModel


class PublicStats(BaseModel):
    """Headline counts of active records for the public page. Counts only: no names,
    ids or other personal data."""

    students: int
    teachers: int
    classes: int
