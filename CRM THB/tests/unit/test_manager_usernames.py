from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat

from handlers.managers import ManagersHandler
from services.admin_client.manager_admin_service import ManagerAdminService


async def test_manager_list_live_usernames_fallbacks_without_display_names():
    rows = [
        {'user_id':1,'display_name':'<Manager>','username':'old_nick'},
        {'user_id':2,'display_name':'No nick','username':'removed_nick'},
        {'user_id':3,'display_name':'3','username':'cached_nick'},
        {'user_id':4,'display_name':'Unknown'},
    ]
    profiles = {1:{'username':'new_nick'},2:{'username':None},3:None,4:None}
    async def resolve(uid):
        return profiles[uid]
    service = ManagerAdminService(SimpleNamespace(list_managers=AsyncMock(return_value=rows)))
    result = await service.handle_command('/mgr',profile_resolver=resolve)
    assert '<code>1</code> — @new_nick' in result
    assert 'Manager' not in result and 'No nick' not in result and 'Unknown' not in result
    assert 'old_nick' not in result and 'removed_nick' not in result
    assert '<code>2</code> — ник не указан' in result
    assert '<code>3</code> — @cached_nick (последний известный)' in result
    assert '<code>4</code> — ник недоступен' in result
    assert ' — 3' not in result


async def test_manager_add_acknowledgement_includes_telegram_nick():
    repo=SimpleNamespace(add_manager=AsyncMock(return_value=True),
        list_managers=AsyncMock(return_value=[{'user_id':777,'display_name':'<Staff>'}]))
    service=ManagerAdminService(repo)
    resolver=AsyncMock(return_value={'username':'staff_nick'})
    result=await service.handle_command('/mgr + 777 <Staff>',profile_resolver=resolver)
    assert result=='✅ Добавлен менеджер: <code>777</code> — @staff_nick'
    resolver.assert_awaited_once_with(777)


async def test_manager_remove_acknowledgement_includes_telegram_nick():
    repo=SimpleNamespace(remove_manager=AsyncMock(return_value=True))
    service=ManagerAdminService(repo)
    resolver=AsyncMock(return_value={'username':'staff_nick'})
    result=await service.handle_command('/mgr - 777',profile_resolver=resolver)
    assert result=='✅ Удалён менеджер: <code>777</code> — @staff_nick'
    resolver.assert_awaited_once_with(777)


@pytest.mark.parametrize('group_available',[True,False])
async def test_handler_falls_back_to_group_or_archive_on_private_lookup_error(group_available):
    repo=SimpleNamespace(list_managers=AsyncMock(return_value=[{
        'user_id':777,'display_name':'Staff','username':'last_nick'}]))
    service=ManagerAdminService(repo)
    error=TelegramBadRequest(method=GetChat(chat_id=777),message='chat not found')
    bot=SimpleNamespace(get_chat=AsyncMock(side_effect=error),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(user=SimpleNamespace(username='group_nick'))))
    if not group_available:
        bot.get_chat_member.side_effect=error
    answer=AsyncMock()
    message=SimpleNamespace(chat=SimpleNamespace(id=-900),message_id=1,from_user=SimpleNamespace(id=12345),
        text='/mgr',bot=bot,answer=answer)
    await ManagersHandler(service,-900)._cmd_mgr(message)
    text=answer.call_args.args[0]
    assert ('@group_nick' if group_available else '@last_nick (последний известный)') in text
    assert answer.call_args.kwargs['parse_mode']=='HTML'


async def test_manager_command_outside_admin_chat_does_not_lookup_profiles():
    service=SimpleNamespace(handle_command=AsyncMock())
    message=SimpleNamespace(chat=SimpleNamespace(id=-1),answer=AsyncMock())
    await ManagersHandler(service,-900)._cmd_mgr(message)
    service.handle_command.assert_not_awaited()
    assert 'только в админском чате' in message.answer.call_args.args[0]
