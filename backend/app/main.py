from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin_skills
from app.api import artifacts
from app.api import conversations
from app.api import health
from app.api import uploads
from app.api.state import runtime
from app.api.state import settings
from app.core.database import init_db
from app.tools.convertx_proxy import router as convertx_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await runtime.start()
    yield
    await runtime.shutdown()


app = FastAPI(title="Rayue API", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(conversations.router)
app.include_router(artifacts.router)
app.include_router(uploads.router)
app.include_router(admin_skills.router)
app.include_router(convertx_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_origin_regex=r"http://.*:(30[0-9]{2}|807[0-9]|8080)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
