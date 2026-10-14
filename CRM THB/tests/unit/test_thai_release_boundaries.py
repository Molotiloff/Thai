"""Thai release must boot without Russian integrations or reverse layer dependencies."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.thai_maintenance import ThaiDatabaseMaintenance

ROOT = Path(__file__).resolve().parents[2]


def test_thai_entrypoints_import_without_google_or_russian_runtime():
    script = """
import importlib.abc
import sys
class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('google', 'gspread', 'gutils', 'app.thai_runtime', 'app.thai_container', 'config_thai',
                     'db_asyncpg.repositories.clients', 'db_asyncpg.repositories.transactions', 'services.rate_order', 'api.routers')
        if fullname.startswith('handlers.') and fullname not in (
            'handlers.cash_requests','handlers.accept_short','handlers.wallets',
            'handlers.clients','handlers.balances_clients','handlers.best_change','handlers.payment_watch',
            'handlers.xe','handlers.debug','handlers.calc','handlers.act','handlers.managers','handlers.broadcast_all','handlers.message_archive','handlers.aml',
        ):
            raise RuntimeError('Unadapted bot handler: ' + fullname)
        if any(fullname == name or fullname.startswith(name + '.') for name in forbidden):
            raise RuntimeError('Forbidden Thai dependency: ' + fullname)
sys.meta_path.insert(0, BlockLegacy())
import bot_app
import app.setup_handlers
import api.thai_server
import app.thai_maintenance
from unittest.mock import MagicMock
from app.container import ApplicationContainer
from app.thai_telegram import ThaiTelegramCommands
from app.setup_handlers import create_router
from config import Config
from api.thai_finance import create_thai_finance_api
container = ApplicationContainer.build(MagicMock())
create_router(ThaiTelegramCommands(container, Config('unused', 'x' * 32)))
create_thai_finance_api(container=container, jwt_secret='x' * 32)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    for obsolete in ('api/run.py','handlers/city.py','handlers/office_cards.py',
                     'handlers/grinex_book.py','app/office_cards.py'):
        assert not (ROOT / obsolete).exists()


def test_thai_application_services_do_not_depend_on_infrastructure():
    packages = ("finance", "capital", "clients", "expenses", "best_change", "exchange", "accounting", "cash_requests")
    forbidden = {
        "api",
        "app",
        "config",
        "config_thai",
        "asyncpg",
        "fastapi",
        "aiogram",
        "google",
        "gspread",
    }
    violations = []
    for package in packages:
        for path in (ROOT / "services" / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                )
                if any(name.split(".")[0] in forbidden or (name.startswith("db_asyncpg.") and not name.startswith("db_asyncpg.ports")) for name in names):
                    violations.append(str(path.relative_to(ROOT)))
    assert not violations


@pytest.mark.parametrize(
    "url",
    ["postgresql://user:secret@localhost:5432/thai", "postgresql://user:secret@localhost/thai"],
)
def test_maintenance_refuses_russian_postgresql_port(url):
    with pytest.raises(ValueError, match="5432"):
        ThaiDatabaseMaintenance(url)
