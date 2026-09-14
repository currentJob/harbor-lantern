"""Application factory for local hosting and HTTPS tunnel access."""

import os
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from harbor_lantern.api.errors import install_error_handlers
from harbor_lantern.api.routes import expenses, external, meta, spots, trips
from harbor_lantern.clock import Clock, SystemClock
from harbor_lantern.config import Settings, load_settings
from harbor_lantern.services.external.cache import CachedProvider
from harbor_lantern.services.external.frankfurter import FrankfurterFxAdapter
from harbor_lantern.services.external.openmeteo import OpenMeteoWeatherAdapter
from harbor_lantern.storage.db import Database


def create_app(*, settings: Settings | None = None, clock: Clock | None = None,
               allowed_origins: list[str] | None = None) -> FastAPI:
    settings = settings or load_settings()
    clock = clock or SystemClock()
    db = Database(settings.db_path)
    db.apply_schema()
    app = FastAPI(title="Harbor Lantern", version="0.1.0")
    app.state.settings = settings
    app.state.clock = clock
    app.state.db = db
    cfg = settings.external
    app.state.weather_provider = CachedProvider(
        OpenMeteoWeatherAdapter(cfg), "weather", cfg.weather_ttl_s, db, clock)
    app.state.fx_provider = CachedProvider(FrankfurterFxAdapter(cfg), "fx", cfg.fx_ttl_s, db, clock)
    origins = allowed_origins if allowed_origins is not None else [
        item.strip() for item in os.environ.get("HL_ALLOWED_ORIGINS", "").split(",") if item.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-Participant-Token", "If-None-Match"],
                       expose_headers=["ETag"])
    install_error_handlers(app)

    @app.middleware("http")
    async def private_responses(request, call_next):
        # `X-Process-Time` 은 성능 하네스가 읽는 값이다(DSN-23 · §8) — 측정 대상은
        # **서버 핸들러 내부 처리시간**이고 네트워크·브라우저는 포함하지 않는다.
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{(perf_counter() - started) * 1000:.3f}"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    for router in (trips.router, spots.router, expenses.router, external.router, meta.router):
        app.include_router(router)
    web = Path(__file__).resolve().parents[1] / "web"
    app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
