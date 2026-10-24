from app.models.academic import (
    AcademicYear,
    ClassSubject,
    SchoolClass,
    Subject,
    Term,
)
from app.models.accounts import Teacher, TeacherClass, TeacherSubject, User
from app.models.audit import AuditLog
from app.models.fees import FeePayment, FeeRecord
from app.models.results import Result
from app.models.students import ParentStudent, Student

__all__ = [
    "AcademicYear",
    "AuditLog",
    "ClassSubject",
    "FeePayment",
    "FeeRecord",
    "ParentStudent",
    "Result",
    "SchoolClass",
    "Student",
    "Subject",
    "Teacher",
    "TeacherClass",
    "TeacherSubject",
    "Term",
    "User",
]
