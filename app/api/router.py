from fastapi import APIRouter

from app.api.routes import files

api_router = APIRouter()
api_router.include_router(files.router)
