from datetime import date

from pydantic import BaseModel, ConfigDict


class ClassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    level: str


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TermOut(BaseModel):
    id: int
    name: str
    academic_year_id: int
    academic_year: str
    is_current_year: bool
    start_date: date | None
    end_date: date | None


class TermList(BaseModel):
    items: list[TermOut]
