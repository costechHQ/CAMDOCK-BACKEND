from fastapi import APIRouter

from app.routers import (
    auth,
    classes,
    fee_types,
    fees,
    health,
    results,
    students,
    subjects,
    teachers,
    terms,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(classes.router)
api_router.include_router(fee_types.router)
api_router.include_router(fees.router)
api_router.include_router(health.router)
api_router.include_router(results.router)
api_router.include_router(students.router)
api_router.include_router(subjects.router)
api_router.include_router(teachers.router)
api_router.include_router(terms.router)
api_router.include_router(users.router)
