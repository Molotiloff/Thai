"""Telegram OIDC session for the independent Thai user directory."""

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field

from api.auth import AuthError, create_access_token
from api.telegram_oidc import exchange_telegram_oidc_code

SESSION_SECONDS = 3600
CALLBACK_PATH = "/api/auth/telegram/oidc/callback"


class TelegramOidcRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
    redirect_uri: str = Field(min_length=1, max_length=2048)
    nonce: str = Field(min_length=16, max_length=256)


def register_auth_routes(app, container, jwt_secret, config):
    @app.post("/api/v1/auth/telegram/oidc")
    async def telegram_login(payload: TelegramOidcRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not config or not all(
            (
                config.telegram_oidc_client_id,
                config.telegram_oidc_client_secret,
                config.frontend_origin,
            )
        ):
            raise HTTPException(503, detail="Telegram OIDC is not configured for Thai CRM")
        expected_redirect = config.frontend_origin.rstrip("/") + CALLBACK_PATH
        if payload.redirect_uri != expected_redirect:
            raise HTTPException(422, detail="Telegram redirect URI does not match Thai CRM")
        try:
            telegram_id = await exchange_telegram_oidc_code(
                code=payload.code,
                code_verifier=payload.code_verifier,
                redirect_uri=expected_redirect,
                expected_nonce=payload.nonce,
                client_id=config.telegram_oidc_client_id,
                client_secret=config.telegram_oidc_client_secret,
            )
        except AuthError:
            raise HTTPException(401, detail="Telegram authentication failed") from None
        actor_id = await container.queries.actor_by_telegram_id(telegram_id)
        if actor_id is None or not await container.queries.can_read(actor_id):
            raise HTTPException(403, detail="Telegram account has no access to Thai CRM")
        return {
            "access_token": create_access_token(
                tg_user_id=telegram_id, secret=jwt_secret, ttl_seconds=SESSION_SECONDS
            ),
            "token_type": "bearer",
            "expires_in": SESSION_SECONDS,
        }
