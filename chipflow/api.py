import logging
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError

from . import __version__
from .config import Project, load_project
from .customization import TCL_POINTS, supported_points, variable_catalog
from .engine import TERMINAL, Store
from .environment import activate_environment, discover_orfs, flow_path, init_project
from .learning import GLOSSARY, LESSONS
from .projects import (
    PreflightError,
    absolute_project,
    create_project,
    inspect_project,
    list_projects,
    workspace,
)
from .setup import assess
from .storage import SCHEMA_VERSION, read_log
from .web_security import LocalBoundary


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: str
    target: str = "sim"


class ProjectYaml(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str


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

    @app.get("/api/guide")
    def guide():
        return {"lessons": LESSONS, "glossary": GLOSSARY}

    @app.get("/api/reference")
    def reference(orfs: str | None = None):
        root = orfs or discover_orfs()
        available = supported_points(flow_path(root))
        return {
            "hooks": {
                name: {**info, "available": name in available} for name, info in TCL_POINTS.items()
            },
            "variables": variable_catalog(root),
        }

    @app.post("/api/projects/starter", status_code=201)
    def starter():
        import uuid

        report = assess(target="sim", versions=False)
        if not report["ready"]:
            raise PreflightError(
                [
                    check["detail"] + ". " + check["action"]
                    for check in report["checks"]
                    if check["status"] != "available"
                ]
            )
        config = init_project(projects_folder / ("first-chip-" + uuid.uuid4().hex[:8]))
        return {"path": str(config), "name": "counter_demo"}

    @app.get("/api/projects")
    def projects():
        return list_projects(projects_folder)

    @app.post("/api/projects", status_code=201)
    def project_create(project: Project):
        return create_project(projects_folder, project)

    @app.get("/api/projects/yaml")
    def project_yaml(config: str):
        project, base = load_project(Path(config))
        return {
            "yaml": yaml.safe_dump(absolute_project(project, base), sort_keys=False),
            "source": str(Path(config).expanduser().resolve()),
        }

    @app.post("/api/projects/yaml", status_code=201)
    def project_yaml_save(body: ProjectYaml):
        try:
            data = yaml.safe_load(body.yaml)
        except yaml.YAMLError as exc:
            raise ValueError(
                "The project YAML could not be read. Check its indentation and syntax."
            ) from exc
        return create_project(projects_folder, Project.model_validate(data))

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
