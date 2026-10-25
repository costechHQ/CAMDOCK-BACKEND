from fastapi import APIRouter

from app.routers import auth, classes, health, subjects

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(classes.router)
api_router.include_router(health.router)
api_router.include_router(subjects.router)
