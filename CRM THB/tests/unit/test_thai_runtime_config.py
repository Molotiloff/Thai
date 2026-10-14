import pytest

from config import Config
from services.payment_watch.tronscan_gateway import TronscanGateway, TronscanSettings


def test_thai_config_does_not_inherit_old_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "old-bot-secret")
    monkeypatch.setenv("DATABASE_URL", "old-db-5432")
    monkeypatch.setenv("ADMIN_IDS", "123")
    monkeypatch.setenv('ADMIN_CHAT_ID','-123')
    monkeypatch.setenv('CONVERTER_API_TOKEN','old-converter-secret')
    path = tmp_path / "thai.env"
    path.write_text(
        "CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=postgresql://new:secret@127.0.0.1:5433/thai\nCRM_JWT_SECRET="
        + "x" * 32
        + "\n"
    )
    config = Config.load(path)
    assert config.database_url.endswith(":5433/thai")
    assert config.bot_token is None and config.admin_ids == ()
    assert config.admin_chat_id is None and config.converter_api_token is None
    assert "secret" not in repr(config)
    with pytest.raises(RuntimeError):
        Config.load(tmp_path / "missing")


def test_one_confirmation_is_not_frozen_in_thai_gateway():
    gateway = TronscanGateway(settings=TronscanSettings(minimum_confirmations=2))
    gateway._remember_confirmations("tx", 1)
    assert "tx" not in gateway._confirmations_cache
    gateway._remember_confirmations("tx", 2)
    assert gateway._confirmations_cache["tx"] == 2


def test_business_chats_are_loaded_from_selected_env_and_must_differ(tmp_path,monkeypatch):
    monkeypatch.setenv('DANIYAR_CHAT','-777')
    path=tmp_path/'thai.env'
    base='CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=postgresql://new:secret@localhost:5433/thai\nCRM_JWT_SECRET='+'x'*32+'\n'
    path.write_text(base+'BEST_CHANGE_CHAT_ID=-123\nDANIYAR_CHAT=-456\n')
    config=Config.load(path)
    assert config.best_change_chat_id==-123 and config.daniyar_chat_id==-456
    path.write_text(base)
    assert Config.load(path).daniyar_chat_id is None
    for value in ('0','-123'):
        path.write_text(base+f'BEST_CHANGE_CHAT_ID=-123\nDANIYAR_CHAT={value}\n')
        with pytest.raises(RuntimeError):
            Config.load(path)


def test_thai_oidc_settings_are_isolated_and_secret_is_not_printed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_OIDC_CLIENT_SECRET", "russian-secret")
    path = tmp_path / "thai.env"
    path.write_text(
        "CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=postgresql://new:secret@localhost:5433/thai\nCRM_JWT_SECRET="
        + "x" * 32
        + "\nTHAI_TELEGRAM_OIDC_CLIENT_ID=123\nTHAI_TELEGRAM_OIDC_CLIENT_SECRET=thai-secret\nTHAI_FRONTEND_ORIGIN=https://thai.example\n"
    )
    config = Config.load(path)
    assert config.telegram_oidc_client_id == "123"
    assert config.telegram_oidc_client_secret == "thai-secret"
    assert config.frontend_origin == "https://thai.example"
    assert "thai-secret" not in repr(config)


def test_thai_auxiliary_settings_use_only_the_selected_env_file(tmp_path):
    path=tmp_path/'thai.env'
    path.write_text('CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=postgresql://new:secret@localhost:5433/thai\n'
        'CRM_JWT_SECRET='+'x'*32+'\nADMIN_CHAT_ID=-900\nCONVERTER_API_BASE_URL=https://converter.example\n'
        'CONVERTER_API_TOKEN=converter-secret\nTHAI_MESSAGE_ARCHIVE_ENABLED=false\n'
        'THAI_MESSAGE_ARCHIVE_MEDIA_DIR=isolated/media\nTHAI_MESSAGE_ARCHIVE_TEMP_DIR=isolated/exports\n')
    config=Config.load(path)
    assert config.admin_chat_id==-900 and config.converter_api_base_url=='https://converter.example'
    assert config.converter_api_token=='converter-secret' and 'converter-secret' not in repr(config)
    assert not config.message_archive_enabled
    assert config.message_archive_media_dir=='isolated/media'
    assert config.message_archive_temp_dir=='isolated/exports'
