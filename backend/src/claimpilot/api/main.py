"""FastAPI app. Phase 0: health/config only; claims, auth, SSE arrive in week 4."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from claimpilot import __version__
from claimpilot.config import get_settings
from claimpilot.db import check_database


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ClaimPilot API",
        version=__version__,
        description="Synthetic demo. Recommendations only; an adjuster makes final decisions.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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
        }

    return app


app = create_app()
