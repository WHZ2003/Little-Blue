import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs
import httpx
import pytest
from little_blue.bili import BiliRuntime,save_note,repair_quote
from little_blue.bili_api import BiliClient,save_credential
from little_blue.core import Core
from little_blue.storage import Store
from little_blue.memory_transfer import export_memory,import_memory

BV='BV1234567890'
TEXT='这是实际读取到的连续字幕原文，用来说明水母发光的生存策略。'
UID=123456789

def setup(tmp_path):
 c=Core(Store(tmp_path/'data/memory.sqlite3'),client=SimpleNamespace())
 c.settings.online=True
 c.store.put('bili_settings',{'enabled':True,'share_bili':True,'mention_uid':UID,'mention_name':'ExampleUser'})
 c.bili.execute('INSERT INTO bili_visits VALUES(?,?,?,?,?,?,?,?)',(BV,'run',time.time(),'observed','水母','url',TEXT,''))
 save_note(c.store,'run',{'bvid':BV,'quote':TEXT[:20],'note':'发光很有趣','share':True,'share_text':'水母发光的生存策略很有意思。'},
           {BV:{'title':'水母','url':'url','text':TEXT}},False,(UID,'ExampleUser'))
 return c

async def test_post_submits_real_uid_once_and_verifies_public_members(tmp_path):
 requests=[]
 async def transport(r):
  requests.append(r)
  if r.method=='POST':
   body=parse_qs(r.content.decode())
   assert json.loads(body['at_name_to_mid'][0])=={'ExampleUser':UID}
   assert body['message']==['@ExampleUser 水母很有趣。'] and body['csrf']==['csrf-test']
   assert 'SESSDATA=test' in r.headers['cookie']
   return httpx.Response(200,json={'code':0,'data':{'rpid':123}})
  assert 'cookie' not in r.headers
  return httpx.Response(200,json={'code':0,'data':{'root':{'rpid':123,'state':0,'content':{'members':[{'mid':UID}]}}}})
 client=BiliClient(tmp_path,httpx.MockTransport(transport))
 save_credential(client.path,{'sessdata':'test','bili_jct':'csrf-test','dedeuserid':'1'})
 async def info(bvid):return {'aid':456}
 client.info=info
 assert await client.post_mention(BV,'@ExampleUser 水母很有趣。',UID,'ExampleUser')=={'accepted':True,'rpid':'123','aid':456}
 async def page(aid):return {'replies':[]}
 client.public_comment_page=page
 result=await client.verify_mention(456,'123',UID)
 assert result['status']=='visible' and result['public_visible'] and result['mention_recognized']
 assert result['notification_delivered'] is None
 assert len(requests)==2

@pytest.mark.parametrize('failure',[False,True])
async def test_durable_fixed_target_and_no_restart_replay(tmp_path,failure):
 c=setup(tmp_path);calls=[]
 async def post(*args,**kwargs):
  calls.append(args)
  if failure:raise TimeoutError()
  return {'accepted':True,'rpid':'123','aid':456}
 c.bili.client=SimpleNamespace(post_mention=post,bound=lambda:True,login_state='idle')
 c.handoff_requested=True;c.bili.tick_mentions();assert not c.bili.active
 c.handoff_requested=False;c.settings.dnd=True;c.bili.tick_mentions();assert not c.bili.active
 c.settings.dnd=False;c.bili.tick_mentions();await c.bili.job
 assert calls[0][2:]==(UID,'ExampleUser') and calls[0][1].startswith('@ExampleUser ')
 assert c.bili.snapshot()['mentions'][0]['status']==('unconfirmed' if failure else 'accepted')
 c.bili=BiliRuntime(c,c.bili.client);c.bili.tick_mentions();assert len(calls)==1
 await c.close()

async def test_changed_recipient_cancels_candidate_and_copy_cannot_publish(tmp_path):
 c=setup(tmp_path)
 archive=tmp_path/'memory.zip';export_memory(tmp_path/'data',archive)
 target=tmp_path/'copy';import_memory(archive,target)
 restored=Core(Store(target/'memory.sqlite3'),client=SimpleNamespace())
 assert not restored.bili.settings.share_bili
 assert restored.bili.snapshot()['mentions'][0]['status']=='cancelled'
 c.store.put('bili_settings',{'enabled':True,'share_bili':True,'mention_uid':2,'mention_name':'wrong'})
 c.bili.tick_mentions();assert c.bili.snapshot()['mentions'][0]['status']=='cancelled'
 await restored.close();await c.close()

async def test_quote_correction_preserves_note_and_share_and_requires_exact_match():
 answer={'bvid':BV,'quote':'不是逐字的……概括','note':'原始心得','share':True,'share_text':'原始分享'}
 observed={BV:{'text':TEXT}}
 calls=[]
 async def complete(messages):
  calls.append(messages)
  return {'content':json.dumps({'quote':TEXT[:20]})}
 fixed=await repair_quote(SimpleNamespace(complete=complete),answer,observed)
 assert fixed=={**answer,'quote':TEXT[:20]} and len(calls)==1
 assert await repair_quote(SimpleNamespace(complete=complete),fixed,observed)==fixed
 assert len(calls)==1
 async def invalid(messages):return {'content':'{"quote":"仍然是没有出现在字幕里的文字"}'}
 with pytest.raises(ValueError):await repair_quote(SimpleNamespace(complete=invalid),answer,observed)

async def test_public_check_does_not_confuse_plain_text_with_real_at(tmp_path):
 client=BiliClient(tmp_path)
 async def page(aid):return {'replies':[{'rpid':123,'state':0,'content':{'message':'@ExampleUser hi','members':[]}}]}
 client.public_comment_page=page
 result=await client.verify_mention(456,'123',UID)
 assert result['status']=='visible_without_mention' and not result['mention_recognized']

@pytest.mark.parametrize('state,status',[(17,'owner_only'),(11,'pending_review'),(0,'not_public')])
async def test_author_view_does_not_prove_public_visibility(tmp_path,state,status):
 client=BiliClient(tmp_path);calls=[]
 async def page(aid):return {'replies':[]}
 async def root(aid,rpid,authenticated=False):
  calls.append(authenticated)
  return {'root':{'rpid':123,'state':state,'invisible':False,'content':{'members':[{'mid':str(UID)}]}}} if authenticated else {}
 client.public_comment_page=page;client.comment_root=root
 result=await client.verify_mention(456,'123',UID)
 assert calls==[False,True]
 assert result['status']==status and result['public_visible'] is False
 assert result['author_visible'] and result['mention_recognized'] and result['comment_state']==state

async def test_lookup_failure_is_unknown_not_deleted_or_delivered(tmp_path):
 client=BiliClient(tmp_path)
 async def fail(*args,**kwargs):raise TimeoutError()
 client.public_comment_page=fail;client.comment_root=fail
 result=await client.verify_mention(456,'123',UID)
 assert result['status']=='visibility_unconfirmed' and result['public_visible'] is None
 assert result['notification_delivered'] is None and len(result['errors'])==3

async def test_owner_only_holds_new_posts_across_restart_until_same_receipt_visible(tmp_path):
 c=setup(tmp_path)
 c.bili.execute("UPDATE bili_mentions SET status='accepted',rpid='123',aid=456")
 result={'status':'owner_only','public_visible':False,'author_visible':True,'comment_state':17,'mention_recognized':True}
 async def verify(*args):return result
 c.bili.client=SimpleNamespace(verify_mention=verify,bound=lambda:True,login_state='idle')
 await c.bili.control({'action':'verify'});await c.bili.job
 assert c.bili.snapshot()['mention_hold']=={'rpid':'123','reason':'owner_only'}
 c.bili.execute('INSERT INTO bili_mentions VALUES(?,?,?,?,?,?,NULL,NULL,NULL)',('BV0987654321',time.time(),'text','pending',UID,'ExampleUser'))
 c.bili=BiliRuntime(c,c.bili.client)
 c.bili.tick_mentions();assert not c.bili.active
 result['status']='visibility_unconfirmed'
 await c.bili.control({'action':'verify'});await c.bili.job
 assert c.bili.snapshot()['mention_hold']
 result['status']='visible';result['public_visible']=True
 await c.bili.control({'action':'verify'});await c.bili.job
 assert c.bili.snapshot()['mention_hold'] is None and c.bili.settings.enabled
 assert c.bili.rows("SELECT status FROM bili_mentions WHERE bvid='BV0987654321'")[0]['status']=='pending'
 await c.close()
