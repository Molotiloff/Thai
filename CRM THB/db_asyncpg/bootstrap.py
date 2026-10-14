"""Migration-only bootstrap, independent of Telegram tokens and runtime adapters."""

from __future__ import annotations

import argparse
import asyncio
import json

from dotenv import dotenv_values

from db_asyncpg.migrator import AlembicMigrator
from db_asyncpg.schema_identity import SchemaFamily


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the isolated Thai CRM database")
    parser.add_argument("--schema", choices=list(SchemaFamily), required=True)
    parser.add_argument("--status", action="store_true", help="Read installed identity/revision only")
    parser.add_argument("--config", default=".env.thai", help="Isolated Thai migration configuration")
    args = parser.parse_args()
    env = dotenv_values(args.config, interpolate=False)
    if env.get('CRM_DATABASE_SCHEMA') != 'thai':
        parser.error('Thai migrations require CRM_DATABASE_SCHEMA=thai in the separate config file')
    url = (env.get('DATABASE_URL') or '').strip()
    if not url:
        parser.error("DATABASE_URL is required")
    migrator = AlembicMigrator(url, schema=args.schema)
    if args.status:
        print(json.dumps(asyncio.run(migrator.status())))
    else:
        asyncio.run(migrator.upgrade_to_head())


if __name__ == "__main__":
    main()
