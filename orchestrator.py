"""Orchestration: validate -> persist -> generate -> verify -> test -> record honest result."""
import time
from datetime import datetime, timedelta, timezone

from .. import database as db
from ..models import (CATEGORIES, STATUS_COMPLETED, STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING, TEMPLATE_INFO)
from ..schemas import ProjectCreate
from . import generation_service as gen


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def create_project(payload: ProjectCreate, is_demo: bool = False) -> dict:
    template = CATEGORIES[payload.category]
    with db.connect() as conn:
        return db.insert_project(conn, name=payload.name, slug=gen.slugify(payload.name), category=payload.category,
                                 template=template, description=payload.description, features=payload.features,
                                 is_demo=is_demo)


def generate_project(project_id: str) -> dict:
    """Run the full pipeline. Returns the saved project with files. Never raises for generation failures."""
    with db.connect() as conn:
        project = db.get_project(conn, project_id)
        if not project:
            raise NotFound(project_id)
        if project["status"] == STATUS_RUNNING:
            raise Conflict("Generation is already running for this project.")
        db.update_project(conn, project_id, status=STATUS_RUNNING, error=None, checks=[])
        db.add_event(conn, project_id, "started", f"Generation started for '{project['name']}'", project["is_demo"])

    started = time.monotonic()
    checks: list[dict] = []
    files: list[dict] = []
    error = None
    output_dir = None
    try:
        dest = gen.safe_output_dir(project_id, project["slug"])
        output_dir = str(dest)
        checks.append({"name": "template_selected", "status": "passed",
                       "detail": f"{TEMPLATE_INFO[project['template']][0]} ({project['template']})"})
        gen.remove_output(dest)  # only this project's own directory, only inside GENERATION_DIR
        gen.render_template(project["template"], project, dest)
        files = gen.collect_files(dest)
        checks.append({"name": "files_generated", "status": "passed", "detail": f"{len(files)} files written"})
        checks += gen.verify_files(dest, files)
        if all(c["status"] == "passed" for c in checks):
            checks.append(gen.run_generated_tests(dest))
        failed = [c for c in checks if c["status"] == "failed"]
        if failed:
            error = "; ".join(f"{c['name']}: {c['detail']}" for c in failed)
    except Exception as exc:  # recorded, never swallowed silently
        error = f"{type(exc).__name__}: {exc}"
        checks.append({"name": "generation", "status": "failed", "detail": error})

    status = STATUS_FAILED if error else STATUS_COMPLETED
    with db.connect() as conn:
        db.replace_files(conn, project_id, files if status == STATUS_COMPLETED else [])
        db.update_project(conn, project_id, status=status, error=error, checks=checks, output_dir=output_dir,
                          file_count=len(files) if status == STATUS_COMPLETED else 0,
                          duration_ms=int((time.monotonic() - started) * 1000))
        db.add_event(conn, project_id, "completed" if status == STATUS_COMPLETED else "failed",
                     f"'{project['name']}' {status.lower()}" + (f": {error}" if error else ""), project["is_demo"])
        return {**db.get_project(conn, project_id), "files": db.get_files(conn, project_id)}


def get_project(project_id: str) -> dict:
    with db.connect() as conn:
        project = db.get_project(conn, project_id)
        if not project:
            raise NotFound(project_id)
        return {**project, "files": db.get_files(conn, project_id)}


def build_download(project_id: str) -> tuple[str, bytes]:
    with db.connect() as conn:
        project = db.get_project(conn, project_id)
        if not project:
            raise NotFound(project_id)
        if project["status"] != STATUS_COMPLETED:
            raise Conflict("Only Completed projects can be downloaded.")
        files, out = db.get_files(conn, project_id), db.get_output_dir(conn, project_id)
    dest = gen.safe_output_dir(project_id, project["slug"])
    if not out or str(dest) != out or not dest.is_dir():
        raise Conflict("Generated files are no longer on disk; regenerate the project.")
    return f"{dest.name}.zip", gen.build_zip(dest, files)


def delete_project(project_id: str) -> None:
    with db.connect() as conn:
        project = db.get_project(conn, project_id)
        if not project:
            raise NotFound(project_id)
        if project["status"] == STATUS_RUNNING:
            raise Conflict("Cannot delete a project while it is generating.")
    gen.remove_output(gen.safe_output_dir(project_id, project["slug"]))
    with db.connect() as conn:
        db.delete_project(conn, project_id)
        db.add_event(conn, None, "deleted", f"Project '{project['name']}' deleted", project["is_demo"])


def analytics(include_demo: bool = False) -> dict:
    with db.connect() as conn:
        projects = db.list_projects(conn, include_demo=include_demo)
        events = [dict(r) for r in conn.execute(
            "SELECT project_id,kind,message,is_demo,created_at FROM events "
            + ("" if include_demo else "WHERE is_demo=0 ") + "ORDER BY id DESC LIMIT 10")]
        days = [dict(r) for r in conn.execute(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS count FROM projects "
            + ("" if include_demo else "WHERE is_demo=0 ") + "GROUP BY day ORDER BY day DESC LIMIT 7")]
        demo_count = conn.execute("SELECT COUNT(*) FROM projects WHERE is_demo=1").fetchone()[0]
    by_status = {s: 0 for s in (STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)}
    by_category = {c: 0 for c in CATEGORIES}
    for p in projects:
        by_status[p["status"]] += 1
        by_category[p["category"]] += 1
    finished = by_status[STATUS_COMPLETED] + by_status[STATUS_FAILED]
    durations = [p["duration_ms"] for p in projects if p["duration_ms"] is not None]
    today = datetime.now(timezone.utc).date()
    week = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        on_day = [p for p in projects if p["created_at"][:10] == day.isoformat()]
        week.append({"day": day.isoformat(), "label": day.strftime("%a"),
                     "real": sum(not p["is_demo"] for p in on_day), "demo": sum(p["is_demo"] for p in on_day)})
    return {
        "weekly_activity": week,
        "total_projects": len(projects), "by_status": by_status, "by_category": by_category,
        "success_rate": round(by_status[STATUS_COMPLETED] / finished * 100, 1) if finished else None,
        "total_files_generated": sum(p["file_count"] for p in projects if p["status"] == STATUS_COMPLETED),
        "avg_generation_ms": round(sum(durations) / len(durations)) if durations else None,
        "projects_per_day": sorted(days, key=lambda d: d["day"]), "recent_activity": events,
        "demo_projects_total": demo_count, "includes_demo": include_demo,
    }
