import logging
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from . import __version__
from .config import Project
from .engine import TERMINAL, Store
from .environment import activate_environment
from .projects import PreflightError, create_project, inspect_project, list_projects, workspace
from .setup import assess
from .storage import SCHEMA_VERSION, read_log
from .web_security import LocalBoundary


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: str
    target: str = "sim"


def create_app(root: Path | str = ".chipflow/runs"):
    activate_environment()
    store = Store(root)
    app = FastAPI(title="Chipflow", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(LocalBoundary)
    app.state.store = store
    projects_folder = store.root.parent / "projects"

    @app.get("/api/health")
    def health():
        if not store.root.is_dir() or not os.access(store.root, os.R_OK | os.W_OK | os.X_OK):
            return JSONResponse(
                {"status": "unavailable", "detail": "Run store is not accessible"}, status_code=503
            )
        return {"status": "ok", "version": __version__, "run_schema_version": SCHEMA_VERSION}

    @app.exception_handler(OSError)
    async def storage_failure(request, exc):
        logging.getLogger(__name__).error("Local storage access failed: %s", exc)
        return JSONResponse(
            {
                "detail": "Local storage access failed. Check permissions, free space, and the server log."
            },
            status_code=503,
        )

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
        path = store.artifact(run_id, "console.log")
        result = read_log(path, offset, final=store.get(run_id)["status"] in TERMINAL)
        return {"text": result["text"], "offset": result["offset"]}

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
