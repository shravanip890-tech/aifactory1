import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .database import init_db
from .routers import projects

log = logging.getLogger("parivestra")


def err(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse({"success": False, "data": None,
                         "error": {"code": code, "message": message, "details": details}}, status_code=status)


def create_app() -> FastAPI:
    app = FastAPI(title="Parivestra AI Software Factory", version="1.0.0",
                  description="Generates real starter projects from templates. Docs at /docs.")
    init_db()

    @app.middleware("http")
    async def limit_size(request: Request, call_next):
        limit = get_settings().max_request_bytes
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return err(413, "payload_too_large", f"Request body exceeds {limit} bytes.")
        if request.method in {"POST", "PUT", "PATCH"} and not length:
            body = await request.body()
            if len(body) > limit:
                return err(413, "payload_too_large", f"Request body exceeds {limit} bytes.")
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(_, exc):
        return err(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(_, exc):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return err(422, "validation_error", "Invalid request.", details)

    @app.exception_handler(Exception)
    async def unhandled(_, exc):
        log.exception("Unhandled error")  # details stay in server log only
        return err(500, "internal_error", "Internal server error.")

    @app.get("/health")
    def health():
        return {"success": True, "data": {"status": "ok"}, "error": None}

    app.include_router(projects.router)
    return app


app = create_app()
