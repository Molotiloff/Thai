"""Create an isolated local PostgreSQL instance for Thai CRM, away from port 5432."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = 5433


async def _create_roles_and_database(admin_password: str, app_password: str,
                                     test_password: str) -> None:
    connection = await asyncpg.connect(host="127.0.0.1", port=PORT, database="postgres",
                                       user="thai_admin", password=admin_password)
    try:
        # Generated passwords are URL-safe; escape literals explicitly for SQL DDL.
        for role, password, attributes in (
            ("thai_crm", app_password, "LOGIN"),
            ("thai_test", test_password, "LOGIN CREATEDB"),
        ):
            escaped = password.replace("'", "''")
            await connection.execute(f"CREATE ROLE {role} {attributes} PASSWORD '{escaped}'")
        await connection.execute("CREATE DATABASE thai_crm OWNER thai_crm")
    finally:
        await connection.close()


def main() -> None:
    for executable in ("initdb", "pg_ctl"):
        if not shutil.which(executable):
            raise RuntimeError(f"{executable} is required to create the local Thai instance")
    runtime = PROJECT_ROOT / ".runtime"
    data = runtime / "thai-postgres"
    env_file = PROJECT_ROOT / ".env.thai"
    if data.exists() or env_file.exists():
        raise RuntimeError("Thai data/config already exists; refusing to overwrite it")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))  # Occupied port is an error, never use another server.
    runtime.mkdir(mode=0o700, exist_ok=True)
    admin_password, app_password, test_password = (secrets.token_urlsafe(32) for _ in range(3))
    socket_key = hashlib.sha256(str(PROJECT_ROOT).encode()).hexdigest()[:12]
    socket_directory = Path(tempfile.gettempdir()) / f"crm-thai-{socket_key}"
    socket_directory.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix="thai-init-", dir=runtime) as password_file:
        password_file.write(admin_password + "\n")
        password_file.flush()
        subprocess.run([
            "initdb", "-D", str(data), "-U", "thai_admin",
            "--auth-local=scram-sha-256", "--auth-host=scram-sha-256",
            "--encoding=UTF8", "--locale=C", f"--pwfile={password_file.name}",
        ], check=True, capture_output=True)
    socket_setting = str(socket_directory).replace("'", "''")
    with (data / "postgresql.conf").open("a") as configuration:
        configuration.write(
            "\n# Isolated Thai CRM instance\n"
            "listen_addresses = '127.0.0.1'\n"
            f"port = {PORT}\n"
            f"unix_socket_directories = '{socket_setting}'\n"
            "timezone = 'Asia/Bangkok'\n"
        )
    # Keep connection credentials recoverable if initialization is interrupted.
    credentials = runtime / "thai-credentials.json"
    descriptor = os.open(credentials, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump({"admin_password": admin_password, "app_password": app_password,
                   "test_password": test_password, "socket_directory": str(socket_directory)}, stream)
    subprocess.run([
        "pg_ctl", "-D", str(data), "-l", str(runtime / "thai-postgres.log"),
        "-o", f"-h 127.0.0.1 -p {PORT} -k {socket_directory}", "-w", "start",
    ], check=True, capture_output=True)
    asyncio.run(_create_roles_and_database(admin_password, app_password, test_password))
    descriptor = os.open(env_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(
            "# Isolated local Thai PostgreSQL; contains secrets, do not commit.\n"
            f"DATABASE_URL=postgresql://thai_crm:{app_password}@127.0.0.1:{PORT}/thai_crm\n"
            f"THAI_TEST_DATABASE_URL=postgresql://thai_test:{test_password}@127.0.0.1:{PORT}/postgres\n"
            "CRM_DATABASE_SCHEMA=thai\n"
            f"CRM_JWT_SECRET={secrets.token_urlsafe(48)}\n"
            "CRM_API_HOST=127.0.0.1\n"
            "CRM_API_PORT=8001\n"
        )
    print("Thai PostgreSQL created: 127.0.0.1:5433 / thai_crm. Credentials saved to .env.thai.")


if __name__ == "__main__":
    main()
