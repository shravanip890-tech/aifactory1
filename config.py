"""Settings read from environment at call time (so tests can override them)."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    generation_dir: Path
    db_path: Path
    max_request_bytes: int
    run_generated_tests: bool
    generated_tests_timeout: int
    templates_dir: Path


def get_settings() -> Settings:
    def p(name: str, default: str) -> Path:
        path = Path(os.getenv(name, default))
        return path if path.is_absolute() else (ROOT / path)

    return Settings(
        generation_dir=p("GENERATION_DIR", "generated").resolve(),
        db_path=p("DB_PATH", "data/factory.db").resolve(),
        max_request_bytes=int(os.getenv("MAX_REQUEST_BYTES", "65536")),
        run_generated_tests=_bool("RUN_GENERATED_TESTS", False),
        generated_tests_timeout=int(os.getenv("GENERATED_TESTS_TIMEOUT", "120")),
        templates_dir=Path(os.getenv("TEMPLATES_DIR", str(ROOT / "templates"))).resolve(),
    )
