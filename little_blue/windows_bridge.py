"""Windows UI bridge. Attach only to explicitly pinned, already-open chat windows.

No login, contact search, database decryption, DLL injection or arbitrary commands.
The adapter remains experimental until the configured account/version passes the
live checklist. Core activation rejects an incomplete checklist.
"""
import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
import uuid

import httpx
from .bridge import Binding, Incoming

ROOT = Path(__file__).resolve().parents[1]


def parse_bubble(raw, route, self_name, referenced=False):
    """Fail closed on unknown senders, references and ambiguous display names."""
    if referenced or not isinstance(raw,str):
        return None
    aliases = route.members if route.kind=='group' else {route.peer:route.title}
    matches=[(ident,name) for ident,name in aliases.items()
             if raw.startswith(name+'\n') or raw.startswith(name+' ')]
    if len(matches)!=1:
        return None
    ident, name=matches[0]
    if name==self_name:
        return None
    text=raw[len(name):].lstrip(' \n\r')
    if not text or any(x in text for x in ('[图片]','[语音]','[文件]','[视频]')):
        return None
    # UI text does not expose protocol mention IDs. Only this exact separator
    # pattern may be enabled after both positive AND negative real @ tests.
    mention = bool(route.bot_name and re.search(r'(?:^|\s)@'+re.escape(route.bot_name)+'\u2005', text))
    return ident,text[:2000],mention


def unlocked():
    if os.name!='nt':
        return False
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.OpenInputDesktop.restype=ctypes.c_void_p
    user32.SwitchDesktop.argtypes=[ctypes.c_void_p]
    user32.CloseDesktop.argtypes=[ctypes.c_void_p]
    desktop=user32.OpenInputDesktop(0,False,0x0100)
    if not desktop:
        return False
    try:
        return bool(user32.SwitchDesktop(desktop))
    finally:
        user32.CloseDesktop(desktop)


class WindowsUI:
    # Actual 4.1.15.56 probe: text/@ visible, sender and direction absent in UIA.
    # Keep sending disabled until a provider supplies validated sender metadata.
    identity_supported = False
    def __init__(self, config):
        if os.name!='nt':
            raise RuntimeError('Windows required')
        from pywinauto import Desktop
        import psutil
        self.desktop, self.psutil = Desktop(backend='uia'), psutil
        self.config, self.binding = config, Binding.model_validate(config['binding'])
        self.epoch=uuid.uuid4().hex
        self.seen={}
        self.failed_session=False

    def window(self, route):
        handle=self.config['windows'][route.id]
        window=self.desktop.window(handle=handle)
        if not window.exists(timeout=.2) or window.window_text()!=route.title:
            raise RuntimeError('Pinned chat changed or closed')
        if window.process_id()!=self.config['pid']:
            raise RuntimeError('Chat belongs to a different process')
        return window

    def health(self):
        if not unlocked():
            self.seen.clear()
            return 'locked'
        if self.failed_session:
            return 'logged_out'
        try:
            process=self.psutil.Process(self.config['pid'])
            if process.name().lower()!='weixin.exe' or abs(process.create_time()-self.config['process_started'])>.1:
                self.failed_session=True
                return 'logged_out'
            main=self.desktop.window(handle=self.config['main_window'])
            if not main.exists(timeout=.2) or not main.child_window(auto_id='MainView').exists(timeout=.2):
                self.failed_session=True
                return 'logged_out'
            if any(re.search('网络连接已断开|网络连接不可用|正在连接|Disconnected|Connecting',t.window_text())
                   for t in main.descendants(control_type='Text')):
                self.seen.clear()
                return 'disconnected'
            for route in self.binding.routes:
                window=self.window(route)
                if len([x for x in window.descendants(control_type='List') if x.element_info.automation_id=='chat_message_list'])!=1:
                    return 'unreadable'
                if len([x for x in window.descendants(control_type='Edit') if x.element_info.automation_id=='chat_input_field'])!=1:
                    return 'unreadable'
            return 'ready' if self.identity_supported else 'unreadable'
        except Exception:
            self.seen.clear()
            return 'unreadable'

    def rows(self, route):
        window=self.window(route)
        lists=[x for x in window.descendants(control_type='List') if x.element_info.automation_id=='chat_message_list']
        if len(lists)!=1:
            raise RuntimeError('Message list ambiguous')
        return lists[0].children(control_type='ListItem')

    def incoming(self):
        events=[]
        for route in self.binding.routes:
            rows=self.rows(route)
            current={}
            for row in rows:
                raw=row.window_text()
                rid=json.dumps(row.element_info.runtime_id)
                key=hashlib.sha256((self.epoch+route.id+rid+raw).encode()).hexdigest()
                current[key]=(raw,row.class_name())
            if route.id not in self.seen:
                self.seen[route.id]=set(current)
                continue  # Initial view / reconnect is a baseline, never history replay.
            new=set(current)-self.seen[route.id]
            self.seen[route.id].update(current)
            if len(self.seen[route.id])>10000:
                raise RuntimeError('Restart bridge to renew bounded message baseline')
            for key in current:
                if key not in new:
                    continue
                raw,kind=current[key]
                parsed=parse_bubble(raw,route,self.config['self_name'],kind!='mmui::ChatBubbleItemView')
                if not parsed:
                    continue
                sender,text,mention=parsed
                events.append(Incoming(account=self.binding.account,route=route.id,sender=sender,
                    id=key,text=text,created=time.time(),mention=mention,
                    mention_evidence='verified_inline' if mention else 'none'))
        return events

    def send(self, command):
        route=next(r for r in self.binding.routes if r.id==command['route']['id'])
        if command['route']!=route.model_dump() or self.health()!='ready':
            return 'failed'
        try:
            window=self.window(route)
            edits=[x for x in window.descendants(control_type='Edit') if x.element_info.automation_id=='chat_input_field']
            if len(edits)!=1 or edits[0].window_text().strip():
                return 'failed'  # Never overwrite a human draft.
            before={tuple(r.element_info.runtime_id) for r in self.rows(route)}
            edits[0].set_edit_text(command['text'])
            if edits[0].window_text()!=command['text'] or self.health()!='ready':
                return 'failed'
            edits[0].type_keys('{ENTER}',set_foreground=True)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if self.health()!='ready':
                    return 'unknown'
                rows=self.rows(route)
                echoed=any(tuple(r.element_info.runtime_id) not in before and command['text'] in r.window_text() for r in rows)
                if echoed:
                    # This is a visible local outgoing bubble, NOT a recipient read receipt.
                    if command.get('image'):
                        self.send_image(route,command['image'])
                    return 'sent'
                time.sleep(.25)
            return 'unknown'
        except Exception:
            return 'unknown'  # Never retry uncertain Enter/send outcomes.

    def send_image(self, route, encoded):
        """Bounded registered meme only; preserve clipboard or refuse the image."""
        import win32clipboard as clip
        from PIL import Image
        if len(encoded)>12*1024*1024 or self.health()!='ready':
            return False
        data=base64.b64decode(encoded,validate=True)
        with Image.open(io.BytesIO(data)) as im:
            if im.width*im.height>16000000:
                return False
            out=io.BytesIO()
            im.convert('RGB').save(out,format='BMP')
            dib=out.getvalue()[14:]
        old=[]
        clip.OpenClipboard()
        try:
            fmt=clip.EnumClipboardFormats(0)
            while fmt:
                value=clip.GetClipboardData(fmt)
                if not isinstance(value,(str,bytes)):
                    return False
                old.append((fmt,value))
                fmt=clip.EnumClipboardFormats(fmt)
            clip.EmptyClipboard()
            clip.SetClipboardData(8,dib)
        finally:
            clip.CloseClipboard()
        try:
            edit=self.window(route).child_window(control_type='Edit',auto_id='chat_input_field')
            if edit.window_text().strip() or self.health()!='ready':
                return False
            edit.type_keys('^v',set_foreground=True)
            time.sleep(.5)
            if self.health()!='ready':
                return False
            edit.type_keys('{ENTER}',set_foreground=True)
            return True
        finally:
            clip.OpenClipboard()
            try:
                # Do not clobber a clipboard the user changed while we sent the image.
                if clip.IsClipboardFormatAvailable(8) and clip.GetClipboardData(8)==dib:
                    clip.EmptyClipboard()
                    for fmt,value in old:
                        clip.SetClipboardData(fmt,value)
            finally:
                clip.CloseClipboard()


class BridgeClient:
    def __init__(self, config, driver, http=None):
        self.config,self.driver=config,driver
        self.instance=uuid.uuid4().hex
        self.binding=Binding.model_validate(config['binding'])
        self.http=http or httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=5,
            headers={'Authorization':'Bearer '+(ROOT/'.secrets/bridge_token').read_text().strip()})

    def request(self, action, **data):
        r=self.http.post('/bridge',json={'action':action,'instance':self.instance,**data})
        r.raise_for_status()
        return r.json()

    def step(self):
        status=self.driver.health()
        state=self.request('heartbeat',account=self.binding.account,version=self.binding.version,status=status)
        if status!='ready' or not state['ready']:
            return state
        for event in self.driver.incoming():
            self.request('receive',event=event.model_dump())
        items=self.request('pull')['items']
        for item in items:
            if self.driver.health()!='ready':
                break
            command=self.request('begin',id=item['id'])
            if command.get('wait'):
                continue
            result=self.driver.send(command)
            self.request('ack',id=item['id'],result=result)
        return state


def main():
    parser=argparse.ArgumentParser(description='Experimental Windows WeChat bridge; no credentials required')
    parser.add_argument('command',choices=['probe','run','bind','status','activate','pause','resume','ilink'])
    parser.add_argument('--config',type=Path,default=ROOT/'data/windows-bridge.json')
    args=parser.parse_args()
    try:
        if args.command not in ('probe','run'):
            body={'op':'bridge','host':uuid.uuid4().hex,'bridge_action':args.command}
            if args.command=='bind':
                body['value']=json.loads(args.config.read_text(encoding='utf-8'))['binding']
            with httpx.Client(trust_env=False,timeout=15) as client:
                r=client.post('http://127.0.0.1:8765/rpc',json=body,
                    headers={'Authorization':'Bearer '+(ROOT/'.secrets/core_token').read_text().strip()})
                r.raise_for_status()
                print(json.dumps(r.json(),ensure_ascii=True))
            return
        config=json.loads(args.config.read_text(encoding='utf-8'))
        driver=WindowsUI(config)
        if args.command=='probe':
            status=driver.health()
            print(json.dumps({'status':status,'configured_routes':len(driver.binding.routes),
                              'sender_metadata_supported':driver.identity_supported,
                              'live_send_tested':False,'activation_ready':False}))
            return
        bridge=BridgeClient(config,driver)
        try:
            while True:
                try:
                    bridge.step()
                except Exception as error:
                    # Never print message text, config, HTTP body or credentials.
                    print('Bridge unavailable: '+type(error).__name__,flush=True)
                    driver.seen.clear()
                time.sleep(1)
        finally:
            bridge.http.close()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print('Bridge not started: '+type(error).__name__)
        raise SystemExit(1)


if __name__=='__main__':
    main()
