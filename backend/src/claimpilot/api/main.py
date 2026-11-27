"""FastAPI app: auth, claims, review actions, audit, SSE progress, metrics.

uv run uvicorn claimpilot.api.main:app --reload     # docs at http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm

from claimpilot import __version__, tracing
from claimpilot.api.claims import metrics_router
from claimpilot.api.claims import router as claims_router
from claimpilot.auth import CurrentUser, authenticate, current_user, issue_token
from claimpilot.checkpoint import open_checkpointer
from claimpilot.config import get_settings
from claimpilot.db import check_database, get_engine, get_sessionmaker
from claimpilot.events import EventHub
from claimpilot.graphs.claim import ClaimService
from claimpilot.llm import LLMClient
from claimpilot.tools import ToolClient

NOTICE = "Synthetic demo. Recommendations only; an adjuster makes final decisions."


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    hub = EventHub()
    await hub.start()
    llm, tools = LLMClient(settings), ToolClient(settings)
    llm.add_listener(hub.on_llm)
    tools.add_listener(hub.on_tool)
    async with open_checkpointer() as saver:
        app.state.settings = settings
        app.state.hub = hub
        app.state.tasks = set()
        app.state.service = ClaimService(llm, tools, get_sessionmaker(), saver, hub.on_graph)
        yield
        if app.state.tasks:  # let in-flight claim steps finish their current write
            await asyncio.gather(*app.state.tasks, return_exceptions=True)
    await hub.stop()
    tracing.flush()
    await get_engine().dispose()


def create_app(use_lifespan: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ClaimPilot API",
        version=__version__,
        description=NOTICE,
        lifespan=lifespan if use_lifespan else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # locked to the frontend origin
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        db = await check_database()
        return {
            "status": "ok" if db["ok"] else "degraded",
            "version": __version__,
            "env": settings.env,
            "llm_mode": settings.llm_mode.value,
            "tracing": settings.tracing_enabled,
            "database": db,
            "notice": NOTICE,
        }

    @app.post("/auth/login", tags=["auth"])
    async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> dict:
        user = await authenticate(form.username, form.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
        return {
            "access_token": issue_token(user),
            "token_type": "bearer",
            "role": user.role,
            "name": user.name,
        }

    @app.get("/auth/me", tags=["auth"])
    async def me(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        return user

    app.include_router(claims_router)
    app.include_router(metrics_router)
    return app


app = create_app()
