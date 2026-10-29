import hashlib
import json

import pytest

from little_blue.memes import MemeCatalog


def test_gif_catalog_selection_and_tamper(tmp_path):
    folder=tmp_path/'nekowhale'; folder.mkdir()
    data=b'GIF89a'+b'\0'*20
    path=folder/'n103.gif'; path.write_bytes(data)
    item=dict(id='n103', title='摸鱼', source='nekowhale', format='gif', bytes=len(data),
              git_blob_sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),
              emotion='开心', tags=['休息','摸鱼'], auto_context='休息闲聊')
    (tmp_path/'nekowhale.json').write_text(json.dumps({'items':[item]}),encoding='utf-8')
    catalog=MemeCatalog(tmp_path)
    assert catalog.read('n103')==data
    assert catalog.suggestions('一起摸鱼')[0]['id']=='n103'
    assert catalog.extract('休息一下[[meme:n103]]')==('休息一下','n103')
    assert catalog.requested('发一张摸鱼表情')=='n103'
    assert not catalog.suggestions('不要发表情')
    path.write_bytes(b'GIF89a'+b'1'*20)
    assert not catalog.available()
    with pytest.raises(ValueError): catalog.read('n103')


def test_pack_has_no_hundred_item_truncation(tmp_path):
    items=[dict(id=f'n{i:03}',git_blob_sha='a'*40) for i in range(1,323)]
    (tmp_path/'nekowhale.json').write_text(json.dumps({'items':items}))
    assert len(MemeCatalog(tmp_path).items)==322


async def test_wechat_catalog_paginates_and_searches(tmp_path):
    from little_blue.core import Core
    from little_blue.storage import Store
    from little_blue.wechat import WeChatChannel, Account
    core=Core(Store(tmp_path/'memory.db'))
    class API:
        async def send(self, context, text): replies.append(text)
    replies=[]
    channel=WeChatChannel(core,Account(token='test',owner='test',bot='test'),API())
    items=[dict(id=f'n{i:03}',title=f'表情{i}',source='nekowhale') for i in range(1,30)]
    items[16]['title']='摸鱼'
    channel.memes.available=lambda:items.copy()
    try:
        await channel.meme_control('/memes','ctx')
        assert 'n012' in replies[-1] and 'n013' not in replies[-1]
        await channel.meme_control('/memes 2','ctx')
        assert 'n013' in replies[-1] and 'n025' not in replies[-1]
        await channel.meme_control('/memes 摸鱼','ctx')
        assert 'n017' in replies[-1] and '共1张' in replies[-1]
    finally:core.store.close()
