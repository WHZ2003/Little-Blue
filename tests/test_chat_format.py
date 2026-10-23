import pytest
from little_blue.chat_format import messages


@pytest.mark.parametrize('text', ['好呀，库...', '库...', '第一句\n第二句', '长'*75, '🙂'*31, '数据库和仓库都在。'])
def test_short_single_line_messages(text):
    parts = messages(text)
    assert all(0 < len(p) <= 30 and '\n' not in p and '\r' not in p for p in parts)
    if text == '数据库和仓库都在。':
        assert parts == [text]
    if text == '长'*75:
        assert ''.join(parts) == text
    if text == '第一句\n第二句':
        assert parts == ['第一句', '第二句']
    if text == '好呀，库...':
        assert parts == ['好呀']


async def test_channel_sends_separate_messages_and_does_not_retry_failed_part(tmp_path):
    from little_blue.core import Core
    from little_blue.storage import Store
    from little_blue.companion import exchange, setup
    from little_blue.wechat import Account, WeChatChannel
    core = Core(Store(tmp_path/'memory.db'))
    setup(core.store)
    exchange(core.store, {'client':'f'*32, 'persona':'short messages'})
    class API:
        sent = []
        fail = False
        async def send(self, context, text):
            self.sent.append(text)
            if self.fail and len(self.sent) == 2:
                raise RuntimeError('unknown delivery')
    api = API()
    channel = WeChatChannel(core, Account(token='test',owner='owner',bot='bot'),api)
    assert await channel.send('context','第一句\n第二句，库...')
    assert api.sent == ['第一句','第二句']
    api.sent=[];api.fail=True
    assert not await channel.send('context','第一句\n第二句\n第三句')
    assert api.sent == ['第一句','第二句']
    core.store.close()
