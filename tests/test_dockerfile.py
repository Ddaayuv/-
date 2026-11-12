"""Guard: the Docker image must ship everything init_db() needs to run Alembic."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_copies_alembic_files():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY migrations ./migrations" in text
    assert "COPY alembic.ini ./alembic.ini" in text


def test_dockerignore_does_not_exclude_migrations():
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "migrations" not in patterns
    assert "alembic.ini" not in patterns
