import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8-sig")

spool_directory = Path(
    os.getenv("ENGINEER_WORKSPACE") or Path(__file__).resolve().parent / "workspace"
)
spool_directory.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(spool_directory.resolve())

from services.api import recover_pending_patches, router  # noqa: E402
from services.repository import MAX_BYTES  # noqa: E402


LOCAL_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


class LocalRequestGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin and origin not in LOCAL_ORIGINS:
            await JSONResponse(
                {"detail": "Request origin is not allowed."}, status_code=403
            )(scope, receive, send)
            return
        upload = scope["path"].rstrip("/") == "/api/repos/upload"
        limit = MAX_BYTES + 4 * 1024**2 if upload else 2 * 1024**2
        declared = headers.get(b"content-length")
        if declared is None and upload:
            await JSONResponse(
                {"detail": "Upload requires a bounded Content-Length."}, status_code=411
            )(scope, receive, send)
            return
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if not 0 <= length <= limit:
                await JSONResponse(
                    {"detail": "Invalid or excessive request size."},
                    status_code=413 if length > limit else 400,
                )(scope, receive, send)
                return
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise HTTPException(413, "Body exceeds the request size limit.")
            return message

        await self.app(scope, bounded_receive, send)


@asynccontextmanager
async def lifespan(_app):
    recover_pending_patches()
    yield


app = FastAPI(title="AI Software Engineer Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(LocalRequestGuard)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(LOCAL_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/")
def home():
    return {"message": "AI Software Engineer Agent is running"}


@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "backend"}
