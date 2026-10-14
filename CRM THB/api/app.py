"""The existing API factory shares the same application services as Telegram."""
from api.thai_finance import create_thai_finance_api


def create_api_app(config, *, container):
    return create_thai_finance_api(container=container,jwt_secret=config.crm_jwt_secret,
        cors_origins=list(config.api_cors_origins),oidc_config=config)
