from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from .config import Project
from .engine import Store
from .environment import activate_environment
from .projects import PreflightError, create_project, inspect_project, list_projects, workspace
from .setup import assess


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: str
    target: str = "sim"


def create_app(root: Path | str = ".chipflow/runs"):
    activate_environment()
    store = Store(root)
    app = FastAPI(title="Chipflow", version="0.1.0")
    app.state.store = store
    projects_folder = store.root.parent / "projects"

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get("origin")
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "Loopback host required"}, status_code=403)
        if origin and (
            urlparse(origin).netloc != request.url.netloc
            or urlparse(origin).scheme != request.url.scheme
        ):
            return JSONResponse({"detail": "Same-origin requests required"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests rejected"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Run or artifact not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        if isinstance(exc, PreflightError):
            return JSONResponse(
                {"detail": "Fix these items before running.", "issues": exc.issues}, status_code=400
            )
        if isinstance(exc, ValidationError):
            messages = [
                f"{'.'.join(map(str, e['loc'])) or 'Project'}: {e['msg']}" for e in exc.errors()
            ]
            return JSONResponse(
                {"detail": "Check your project settings.", "issues": messages}, status_code=400
            )
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/workspace")
    def environment():
        return workspace()

    @app.get("/api/setup")
    def setup_report():
        return assess(versions=False)

    @app.get("/api/projects")
    def projects():
        return list_projects(projects_folder)

    @app.post("/api/projects", status_code=201)
    def project_create(project: Project):
        return create_project(projects_folder, project)

    @app.post("/api/projects/inspect")
    def project_inspect(body: StartRun):
        return inspect_project(Path(body.config), body.target)

    @app.post("/api/runs/{run_id}/retry", status_code=202)
    def retry(run_id: str):
        return store.retry(run_id)

    @app.get("/api/runs")
    def history():
        return store.history()

    @app.post("/api/runs", status_code=202)
    def start(body: StartRun):
        return store.start(Path(body.config), body.target)

    @app.get("/api/runs/{run_id}")
    def detail(run_id: str):
        return store.get(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        return store.cancel(run_id)

    @app.get("/api/runs/{run_id}/logs")
    def logs(run_id: str, offset: int = Query(default=0, ge=0)):
        path = store.directory(run_id) / "console.log"
        if not path.exists():
            return {"text": "", "offset": 0}
        with path.open("rb") as f:
            f.seek(min(offset, path.stat().st_size))
            data = f.read(256 * 1024)
            return {"text": data.decode(errors="replace"), "offset": f.tell()}

    @app.get("/api/runs/{run_id}/artifacts")
    def artifacts(run_id: str):
        return store.artifacts(run_id)

    @app.get("/api/runs/{run_id}/artifacts/{relative:path}")
    def artifact(run_id: str, relative: str):
        path = store.artifact(run_id, relative)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/api/compare")
    def compare(left: str, right: str):
        return store.compare(left, right)

    app.mount(
        "/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="dashboard"
    )
    return app
