import pytest

from config import Config


def write_env(tmp_path,extra=''):
    path=tmp_path/'.env.thai'
    path.write_text('CRM_DATABASE_SCHEMA=thai\nDATABASE_URL=postgresql://user:secret@localhost:5433/thai\n'
        'CRM_JWT_SECRET='+'x'*32+'\n'+extra)
    return path


def test_config_reads_existing_crm_keys_from_thai_file(tmp_path):
    path=write_env(tmp_path,'BOT_TOKEN=branch-bot-secret\nADMIN_CHAT_ID=-1\nADMIN_IDS=42,43\nREQUEST_CHAT_ID=-2\n'
        'CASH_CHAT_ID=тай:-3\nCITY_CASH_CHAT_IDS=тай:-4\nCITY_SCHEDULE_CHATS=тай:-5\nBEST_CHANGE_CHAT_ID=-6\n'
        'TELEGRAM_OIDC_CLIENT_ID=123\nTELEGRAM_OIDC_CLIENT_SECRET=oidc-secret\n')
    config=Config.from_env(path)
    assert config.bot_token=='branch-bot-secret' and config.admin_ids==(42,43)
    assert config.request_chat_id==-2 and config.operations_chat_id==-2 and config.admin_chat_id==-1
    assert config.cash_chat_map=={'тай':-3} and config.city_cash_chat_map=={'тай':-4}
    assert config.city_schedule_chats=={'тай':-5} and config.best_change_chat_id==-6
    assert 'branch-bot-secret' not in repr(config) and 'oidc-secret' not in repr(config)


def test_canonical_env_values_take_priority_over_old_branch_aliases(tmp_path):
    config=Config.from_env(write_env(tmp_path,'REQUEST_CHAT_ID=-2\nTHAI_OPERATIONS_CHAT_ID=-20\n'))
    assert config.request_chat_id==-2


def test_oidc_credentials_must_be_configured_together(tmp_path):
    with pytest.raises(RuntimeError,match='должны быть заданы вместе'):
        Config.from_env(write_env(tmp_path,'TELEGRAM_OIDC_CLIENT_ID=123\n'))


def test_config_refuses_other_schema_and_russian_database(tmp_path):
    path=write_env(tmp_path)
    path.write_text(path.read_text().replace('CRM_DATABASE_SCHEMA=thai','CRM_DATABASE_SCHEMA=legacy'))
    with pytest.raises(RuntimeError):
        Config.from_env(path)
    path=write_env(tmp_path)
    path.write_text(path.read_text().replace(':5433/',':5432/'))
    with pytest.raises(ValueError):
        Config.from_env(path)


def test_archive_settings_keep_existing_keys_and_bound_worker_counts(tmp_path):
    config=Config.from_env(write_env(tmp_path,'MESSAGE_ARCHIVE_ENABLED=true\nMESSAGE_ARCHIVE_DOWNLOAD_WORKERS=0\n'
        'MESSAGE_ARCHIVE_EXPORT_WORKERS=0\nMESSAGE_ARCHIVE_EXPORT_PART_BYTES=10\n'))
    assert config.message_archive_enabled
    assert config.message_archive_download_workers==1 and config.message_archive_export_workers==1
    assert config.message_archive_export_part_bytes==1024*1024
