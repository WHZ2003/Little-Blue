import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from little_blue.bili import BiliRuntime, save_note
from little_blue.bili_api import BiliClient, save_credential
from little_blue.core import Core
from little_blue.storage import Store
from little_blue.memory_transfer import export_memory, import_memory

UID=123456789
BV='BV1234567890'
TEXT='这是一段已经实际读过的字幕原文，水母的发光能帮助它生存。'

def setup(tmp_path, share=True):
    c=Core(Store(tmp_path/'data/memory.sqlite3'),client=SimpleNamespace())
    c.bili.client.bound=lambda:True
    c.settings.online=True
    c.store.put('bili_settings',{'enabled':True,'share_dm':True,'dm_uid':UID,'dm_name':'ExampleUser'})
    c.bili.execute('INSERT INTO bili_visits VALUES(?,?,?,?,?,?,?,?)',(BV,'run',time.time(),'observed','水母','url',TEXT,''))
    save_note(c.store,'run',{'bvid':BV,'quote':TEXT[:20],'note':'原始心得','share':share,'share_text':'水母发光的生存策略很有意思。'},
              {BV:{'title':'水母','url':'url','text':TEXT}},dm_target=(UID,'ExampleUser'))
    return c

async def test_dm_single_post_fixed_uid_csrf_and_receipt(tmp_path):
    requests=[]
    async def transport(r):
        requests.append(r)
        assert r.method=='POST' and r.url.host=='api.vc.bilibili.com'
        form=parse_qs(r.content.decode())
        assert form['msg[receiver_id]']==[str(UID)] and form['msg[sender_uid]']==['123']
        assert form['msg[msg_type]']==['1'] and form['msg[receiver_type]']==['1']
        assert json.loads(form['msg[content]'][0])=={'content':'水母心得\nhttps://www.bilibili.com/video/'+BV+'/'}
        assert form['csrf']==['test-csrf'] and 'SESSDATA=test' in r.headers['cookie']
        return httpx.Response(200,json={'code':0,'data':{'msg_key':987654321}})
    client=BiliClient(tmp_path,httpx.MockTransport(transport))
    save_credential(client.path,{'sessdata':'test','bili_jct':'test-csrf','dedeuserid':'123'})
    async def query(sender,receiver):return {'w_sender_uid':sender,'w_receiver_id':receiver,'w_rid':'signed'}
    client.dm_query=query
    result=await client.send_dm('水母心得\nhttps://www.bilibili.com/video/'+BV+'/',UID,permitted=lambda:True)
    assert result=={'accepted':True,'msg_key':'987654321'} and len(requests)==1
    with pytest.raises(asyncio.CancelledError):
        await client.send_dm('水母',UID,permitted=lambda:False)
    assert len(requests)==1

@pytest.mark.parametrize('mode',['accepted','rejected','timeout'])
async def test_dm_gates_persist_receipt_and_never_replay(tmp_path,mode):
    c=setup(tmp_path);calls=[]
    async def send(text,uid,permitted):
        assert permitted();calls.append((text,uid))
        if mode=='timeout':raise TimeoutError()
        return {'accepted':True,'msg_key':'987'} if mode=='accepted' else {'accepted':False,'code':21020}
    c.bili.client=SimpleNamespace(send_dm=send,bound=lambda:True,login_state='idle')
    for field in ['dnd']:
        setattr(c.settings,field,True);c.bili.tick_dms();assert not c.bili.active
        setattr(c.settings,field,False)
    c.handoff_requested=True;c.bili.tick_dms();assert not c.bili.active
    c.handoff_requested=False;c.bili.tick_dms();await c.bili.job
    assert len(calls)==1 and calls[0][1]==UID and 'https://www.bilibili.com/video/'+BV+'/' in calls[0][0]
    assert len(calls[0][0])<=500
    c.bili=BiliRuntime(c,c.bili.client)
    c.bili.tick_dms();assert len(calls)==1 and not c.bili.active
    row=c.bili.snapshot()['direct_messages'][0]
    assert row['status']=={'accepted':'accepted','rejected':'rejected','timeout':'unconfirmed'}[mode]
    assert bool(c.bili.snapshot()['dm_hold'])==(mode!='accepted')
    assert not c.bili.snapshot()['mentions'] and not c.bili.snapshot()['sharing']
    await c.close()

@pytest.mark.parametrize('alter',['none','key','sender','receiver','content'])
async def test_dm_verification_requires_exact_outgoing_message(tmp_path,alter):
    client=BiliClient(tmp_path)
    save_credential(client.path,{'sessdata':'test','dedeuserid':'123'})
    message={'msg_key':'987','sender_uid':123,'receiver_id':UID,'msg_type':1,'content':json.dumps({'content':'心得'})}
    if alter!='none':message[{'key':'msg_key','sender':'sender_uid','receiver':'receiver_id','content':'content'}[alter]]='0'
    async def history(uid):assert uid==UID;return {'messages':[message]}
    client.dm_history=history
    assert await client.verify_dm(UID,'987','心得')==(alter=='none')

async def test_dm_opt_in_and_backup_copy_cannot_send(tmp_path):
    c=setup(tmp_path)
    archive=tmp_path/'memory.zip';export_memory(tmp_path/'data',archive)
    target=tmp_path/'copy';import_memory(archive,target)
    restored=Core(Store(target/'memory.sqlite3'),client=SimpleNamespace())
    assert not restored.bili.settings.share_dm
    assert restored.bili.snapshot()['direct_messages'][0]['status']=='cancelled'
    await restored.close();await c.close()
    other=setup(tmp_path/'no-share',False)
    assert not other.bili.snapshot()['direct_messages']
    await other.close()

async def test_dm_recipient_change_cancels_pending_and_quota_holds(tmp_path):
    c=setup(tmp_path)
    c.store.put('bili_settings',{'enabled':True,'share_dm':True,'dm_uid':2,'dm_name':'other'})
    c.bili.tick_dms();assert c.bili.snapshot()['direct_messages'][0]['status']=='cancelled'
    c.store.put('bili_settings',{'enabled':True,'share_dm':True,'dm_uid':UID,'dm_name':'ExampleUser'})
    c.bili.execute("UPDATE bili_dms SET status='pending'")
    c.bili.execute('INSERT INTO bili_dms VALUES(?,?,?,?,?,?,?,NULL,?)',('BV0987654321',time.time(),'old','recorded',UID,'ExampleUser','987',time.time()))
    c.bili.tick_dms();assert not c.bili.active
    await c.close()

async def test_dm_history_check_records_without_claiming_delivery(tmp_path):
    c=setup(tmp_path)
    c.bili.execute("UPDATE bili_dms SET status='accepted',msg_key='987',created=?",(time.time()-30,))
    async def verify(*args):return True
    c.bili.client=SimpleNamespace(verify_dm=verify,bound=lambda:True,login_state='idle')
    c.bili.tick_dms();await c.bili.job
    assert c.bili.snapshot()['direct_messages'][0]['status']=='recorded'
    await c.close()

async def test_crash_during_dm_post_pauses_further_messages(tmp_path):
    c=setup(tmp_path)
    c.bili.execute("UPDATE bili_dms SET status='sending'")
    c.bili=BiliRuntime(c,c.bili.client)
    assert c.bili.snapshot()['direct_messages'][0]['status']=='unconfirmed'
    assert c.bili.snapshot()['dm_hold']['reason']=='unconfirmed'
    await c.close()

async def test_expired_login_stops_dm_and_does_not_replay_accepted_message(tmp_path):
    c=setup(tmp_path)
    c.bili.account_state='expired';c.bili.tick_dms();assert not c.bili.active
    c.bili.execute("UPDATE bili_dms SET status='accepted',msg_key='987',created=?",(time.time()-30,))
    class Expired(Exception):code=-101
    async def verify(*args):raise Expired()
    c.bili.client=SimpleNamespace(verify_dm=verify,bound=lambda:True,login_state='idle')
    c.bili.account_state='connected';c.bili.tick_dms();await c.bili.job
    assert c.bili.account_state=='expired' and c.bili.snapshot()['direct_messages'][0]['status']=='accepted'
    assert c.bili.error and c.bili.snapshot()['direct_messages'][0]['checked']
    await c.close()
