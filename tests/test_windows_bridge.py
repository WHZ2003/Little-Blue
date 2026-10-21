import httpx
from little_blue.windows_bridge import BridgeClient, WindowsUI, parse_bubble
from little_blue.bridge import Route


def test_unverified_windows_adapter_cannot_report_supported_identity():
    assert WindowsUI.identity_supported is False


def test_text_only_group_bubble_is_not_assigned_an_invented_sender():
    route=Route(id='group',kind='group',title='Test',bot_name='大肥鱼',members={'u1':'Alice','u2':'Bob'})
    assert parse_bubble('@大肥鱼\u2005你好',route,'大肥鱼') is None
    assert parse_bubble('unknown 你好',route,'大肥鱼') is None
    assert parse_bubble('Alice @大肥鱼\u2005你好',route,'大肥鱼',referenced=True) is None


def config():
    return {'binding':{'account':'bot','owner_peer':'owner','version':'test','routes':[
        {'id':'dm','kind':'private','peer':'owner','title':'Owner'}]}}


def test_locked_bridge_never_reads_messages_or_pulls_delivery():
    calls=[]
    class Driver:
        def health(self): return 'locked'
        def incoming(self): raise AssertionError('Must not read while locked')
    def handle(request):
        import json
        calls.append(json.loads(request.content)['action'])
        return httpx.Response(200,json={'ready':False})
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle)) as http:
        BridgeClient(config(),Driver(),http).step()
    assert calls==['heartbeat']


def test_uncertain_send_is_acknowledged_without_retrying():
    calls=[]
    class Driver:
        sent=0
        def health(self): return 'ready'
        def incoming(self): return []
        def send(self, command): self.sent+=1; return 'unknown'
    def handle(request):
        import json
        body=json.loads(request.content)
        calls.append(body)
        if body['action']=='heartbeat': data={'ready':True}
        elif body['action']=='pull': data={'items':[{'id':'outbox','route':'dm'}]}
        elif body['action']=='begin': data={'id':'outbox','route':{'id':'dm'},'text':'hello'}
        else: data={'recorded':True}
        return httpx.Response(200,json=data)
    driver=Driver()
    with httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle)) as http:
        BridgeClient(config(),driver,http).step()
    assert driver.sent==1
    assert calls[-1]['action']=='ack' and calls[-1]['result']=='unknown'
