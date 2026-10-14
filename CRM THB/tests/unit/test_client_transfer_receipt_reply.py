from types import SimpleNamespace

from handlers.wallets import WalletsHandler


def _message(*, caption: str, sender_id: int = 10, has_photo: bool = True):
    return SimpleNamespace(
        bot=SimpleNamespace(id=10),
        reply_to_message=SimpleNamespace(
            from_user=SimpleNamespace(id=sender_id),
            photo=[object()] if has_photo else None,
            caption=caption,
        ),
    )


def test_reply_identifies_own_transfer_receipt() -> None:
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597")) == 597


def test_reply_ignores_other_messages_and_forged_caption() -> None:
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597", sender_id=11)) is None
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597", has_photo=False)) is None
    assert WalletsHandler._replied_transfer_id(_message(caption="Перевод #597 extra")) is None


def test_accounting_parser_preserves_distinct_accounts_and_quoted_expressions():
    from decimal import Decimal

    from app.thai_russian_commands import INSTRUMENTS
    for text,expected in (
        ('/перевод 1к Имя чата',(Decimal('1000'),'THB_CASH','Имя чата')),
        ('/перевод "1000 + 500" безнал Имя чата',(Decimal('1500'),'THB_BANK','Имя чата')),
        ('/перевод 10 USDW Имя чата',(Decimal('10'),'USD_WH','Имя чата')),
        ('/перевод 10 RUB Имя чата',(Decimal('10'),'RUB','Имя чата')),
        ('/перевод 1,123456 USDT Имя чата',(Decimal('1.123456'),'USDT','Имя чата')),
    ):
        assert WalletsHandler._parse_transfer(text,default_currency='THB_CASH',aliases=INSTRUMENTS,
            allow_expressions=True)==expected
