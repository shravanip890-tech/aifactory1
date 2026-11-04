"""Template rendering, path safety, verification and ZIP packaging."""
import hashlib
import io
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from ..config import get_settings
from ..models import CUSTOM_SUPPORTED_FEATURES, TEMPLATE_INFO

REQUIRED_FILES = ["README.md", "requirements.txt", "app/main.py", "tests/test_app.py"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".db", ".sqlite", ".sqlite3"}


class GenerationError(Exception):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def safe_output_dir(project_id: str, slug: str) -> Path:
    """Resolve the project's directory and guarantee it lives inside GENERATION_DIR."""
    if not re.fullmatch(r"[0-9a-f]{12}", project_id) or not re.fullmatch(r"[a-z0-9-]+", slug):
        raise GenerationError("Unsafe project identifier.")
    base = get_settings().generation_dir
    target = (base / f"{project_id}-{slug}").resolve()
    if base not in target.parents:
        raise GenerationError("Resolved output path escapes the generation directory.")
    return target


def ensure_inside(path: Path, base: Path) -> Path:
    resolved = path.resolve()
    if base != resolved and base not in resolved.parents:
        raise GenerationError("Path is outside the generation directory.")
    return resolved


def remove_output(path: Path) -> None:
    """Delete only a directory that is inside GENERATION_DIR."""
    base = get_settings().generation_dir
    resolved = ensure_inside(path, base)
    if resolved == base:
        raise GenerationError("Refusing to remove the generation root.")
    if resolved.exists():
        shutil.rmtree(resolved)


def split_features(template: str, features: list[str]) -> tuple[list[str], list[str]]:
    """Return (supported, unsupported). Only the custom template has selectable features."""
    if template != "custom":
        return [], list(features)
    norm = [f.strip().lower().replace(" ", "_") for f in features]
    supported = [f for f in norm if f in CUSTOM_SUPPORTED_FEATURES]
    unsupported = [orig for orig, n in zip(features, norm) if n not in CUSTOM_SUPPORTED_FEATURES]
    return sorted(set(supported)), unsupported


def render_template(template: str, project: dict, dest: Path) -> None:
    src = get_settings().templates_dir / template
    if template not in TEMPLATE_INFO or not src.is_dir():
        raise GenerationError(f"Template '{template}' is not available.")
    supported, unsupported = split_features(template, project["features"])
    tokens = {
        "__PROJECT_NAME__": project["name"],
        "__PROJECT_SLUG__": project["slug"],
        "__DESCRIPTION__": project["description"] or "(no description provided)",
        "__TEMPLATE_TITLE__": TEMPLATE_INFO[template][0],
        "__FEATURES__": ", ".join(supported) if supported else "(template defaults)",
    }
    routers = []
    for feat in supported:
        if feat != "items":
            routers.append(f"from .{feat} import router as {feat}_router\napp.include_router({feat}_router)")
    tokens["# __OPTIONAL_ROUTERS__"] = "\n".join(routers)
    dest.mkdir(parents=True, exist_ok=False)
    for file in sorted(src.rglob("*")):
        rel = file.relative_to(src)
        if file.is_dir() or SKIP_PARTS & set(rel.parts) or file.suffix in SKIP_SUFFIXES:
            continue
        if rel.parts[0] == "optional":
            feat = rel.stem
            if feat not in supported:
                continue
            rel = Path("app") / rel.name
        text = file.read_text(encoding="utf-8")
        for k, v in tokens.items():
            text = text.replace(k, v)
        out = ensure_inside(dest / rel, dest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if template == "custom" and unsupported:
        lines = ["# Requested features that are NOT implemented", "",
                 "This is a basic starter. The following requested features were not generated:", ""]
        lines += [f"- {u}" for u in unsupported]
        lines += ["", f"Supported custom options: {', '.join(CUSTOM_SUPPORTED_FEATURES)}."]
        (dest / "NOT_IMPLEMENTED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_files(dest: Path) -> list[dict]:
    out = []
    for f in sorted(dest.rglob("*")):
        if f.is_file() and not f.is_symlink() and not (SKIP_PARTS & set(f.parts)) and f.suffix not in SKIP_SUFFIXES:
            data = f.read_bytes()
            out.append({"path": f.relative_to(dest).as_posix(), "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    return out


def verify_files(dest: Path, files: list[dict]) -> list[dict]:
    """Return structured checks. Each: {name, status: passed|failed|skipped, detail}."""
    checks = []
    present = {f["path"] for f in files}
    missing = [r for r in REQUIRED_FILES if r not in present]
    checks.append({"name": "required_files", "status": "failed" if missing else "passed",
                   "detail": f"Missing: {', '.join(missing)}" if missing else f"{len(REQUIRED_FILES)} required files present"})
    empty = [f["path"] for f in files if f["size"] == 0 and not f["path"].endswith("__init__.py")]
    checks.append({"name": "non_empty", "status": "failed" if empty else "passed",
                   "detail": f"Empty files: {', '.join(empty)}" if empty else "No empty files"})
    bad = []
    for f in files:
        if f["path"].endswith(".py"):
            try:
                compile((dest / f["path"]).read_text(encoding="utf-8"), f["path"], "exec")
            except SyntaxError as exc:
                bad.append(f"{f['path']}:{exc.lineno}")
    checks.append({"name": "python_syntax", "status": "failed" if bad else "passed",
                   "detail": f"Syntax errors: {', '.join(bad)}" if bad else "All .py files compile"})
    leftovers = [f["path"] for f in files if "__PROJECT_" in (dest / f["path"]).read_text(encoding="utf-8", errors="ignore")
                 or "__OPTIONAL" in (dest / f["path"]).read_text(encoding="utf-8", errors="ignore")]
    checks.append({"name": "placeholders_resolved", "status": "failed" if leftovers else "passed",
                   "detail": f"Unresolved placeholders in: {', '.join(leftovers)}" if leftovers else "No unresolved placeholders"})
    return checks


def run_generated_tests(dest: Path) -> dict:
    """Run the generated project's pytest suite in a subprocess (opt-in; not a sandbox)."""
    s = get_settings()
    if not s.run_generated_tests:
        return {"name": "generated_tests", "status": "skipped",
                "detail": "Disabled (set RUN_GENERATED_TESTS=true to run the generated project's tests)."}
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=dest,
                              capture_output=True, text=True, timeout=s.generated_tests_timeout,
                              env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONDONTWRITEBYTECODE": "1",
                                   "HOME": str(dest), "APP_DB": str(dest / "test_run.db")})
    except subprocess.TimeoutExpired:
        return {"name": "generated_tests", "status": "failed", "detail": f"Timed out after {s.generated_tests_timeout}s"}
    summary = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    for junk in (dest / "test_run.db",):
        junk.unlink(missing_ok=True)
    return {"name": "generated_tests", "status": "passed" if proc.returncode == 0 else "failed", "detail": summary}


def build_zip(dest: Path, files: list[dict]) -> bytes:
    base = get_settings().generation_dir
    root = ensure_inside(dest, base)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            path = ensure_inside(root / f["path"], root)
            if path.is_file() and not path.is_symlink():
                zf.write(path, arcname=f"{root.name}/{f['path']}")
    return buf.getvalue()
