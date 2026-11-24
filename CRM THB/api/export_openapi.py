"""Export the production Thai API's schema without opening a database connection."""

import json
from typing import cast

from asyncpg import Pool

from api.app import create_api_app
from app.container import ApplicationContainer
from config import Config


def main() -> None:
    config = Config('postgresql://schema@localhost:5433/schema', 'schema-export-' * 3)
    # OpenAPI only inspects route declarations. The lifespan and handlers never run.
    container = ApplicationContainer.build(cast(Pool, None), config=config)
    print(json.dumps(create_api_app(config, container=container).openapi(), sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    main()
