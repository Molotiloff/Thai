"""Disposable databases on an explicitly configured isolated Thai test server."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlunsplit

import pytest
from dotenv import dotenv_values

from db_asyncpg.schema_identity import require_thai_database_url


@contextmanager
def disposable_thai_database(prefix: str, *, locale: str | None = None):
    if not (shutil.which("createdb") and shutil.which("dropdb")):
        pytest.skip("Local createdb/dropdb are required")
    settings = dotenv_values(Path(__file__).resolve().parents[1] / ".env.thai")
    source = os.environ.get("THAI_TEST_DATABASE_URL") or settings.get("THAI_TEST_DATABASE_URL")
    if not source:
        pytest.skip("Set THAI_TEST_DATABASE_URL for the isolated Thai test server")
    parsed = require_thai_database_url(source)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.skip("Disposable tests require a local PostgreSQL server")
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    owner = unquote(parsed.username or "")
    arguments = ["-h", parsed.hostname, "-p", str(parsed.port or 5432), "-U", owner]
    environment = {**os.environ, "PGPASSWORD": unquote(parsed.password or "")}
    database_options = ["--template=template0", f"--locale={locale}"] if locale else []
    subprocess.run(["createdb", *arguments, "-O", owner, *database_options, name],
                   check=True, capture_output=True, env=environment)
    try:
        yield urlunsplit(parsed._replace(path=f"/{name}", query="", fragment=""))
    finally:
        subprocess.run(["dropdb", *arguments, name], check=True,
                       capture_output=True, env=environment)
