import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,readFileSync} from 'node:fs';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {SharedMemory} from './index.mjs';import {attachPreferences,KEY} from './preferences.mjs';
import {shortBeats} from './chat-format.mjs';

function fixture(){
 const dir=mkdtempSync(join(tmpdir(),'prefs-'));mkdirSync(join(dir,'workspace'));
 const shared=new SharedMemory({botDir:dir,dataDir:join(dir,'data'),cfg:{}});shared.stopped=false;
 shared.host={pushEvent:async(e,opts)=>{assert.equal(opts.deliver,false);shared.lastReceipt=e;return {...e,cursor:100};}};
 shared.preferenceBridge=attachPreferences(shared);return shared;
}
const event=(text='以后叫我小丹',cursor=1)=>({source:'desktop-pet',type:'desktop-pet.message',cursor,ts:new Date().toISOString(),meta:{text}});

test('pre-reply hook persists response and shows actual receipt even if model stays silent',async()=>{
 const s=fixture();let calls=0;
 s.transport={request:async p=>{calls++;assert.equal(p.key,'event:1');return {status:'applied',receipt:'已保存称呼小丹。',snapshot:{revision:1,values:{address:'小丹',message_mode:'whole'}}};}};
 await s.preferenceBridge.before([event()]);
 assert.equal(calls,1);assert.equal(s.lastReceipt.text,'已保存称呼小丹。');
 assert.equal(JSON.parse(readFileSync(s.stateFile)).preferences.values.address,'小丹');
 assert.equal(shortBeats([{text:'设置好了',actions:[],anchors:[]}])[0].text,'已保存称呼小丹。');
 assert.deepEqual(shortBeats([{text:'设置好了',actions:[],anchors:[]}]),[]);
 s.onTurnEnded();assert.equal(s.preferenceBridge.receipt,null);
 const beats=shortBeats([{text:'一',actions:['wave'],anchors:[]},{text:'二',actions:['smile'],anchors:[]}]);
 assert.equal(beats.length,1);assert.equal(beats[0].text,'一\n二');assert.deepEqual(beats[0].anchors,[{at:2,actions:['smile']}]);
 delete globalThis[KEY];
});
test('offline preference queue survives reload, retries original event and avoids imported-history changes',async()=>{
 const s=fixture();s.transport={request:async()=>{throw Error('offline');}};
 const original=event();await s.preferenceBridge.before([original]);
 assert.match(s.lastReceipt.text,/是否已生效尚未确认/);assert.equal(s.state.preferencePending.length,1);
 await s.preferenceBridge.before([{...event('以后改名',2),meta:{text:'以后改名',dafeiyuCloudId:5}}]);
 assert.equal(s.state.preferencePending.length,1);
 const restored=new SharedMemory(s.ctx);assert.equal(restored.state.preferencePending.length,1);
 restored.transport={request:async p=>{assert.equal(p.created,Date.parse(original.ts)/1000);return {status:'applied',receipt:'云端已保存；本地在线后自动同步，是否已同步可用“查看偏好”核对。',snapshot:{revision:2,values:{address:'小丹'}}};}};
 const r=await restored.processPreferenceEvent(restored.state.preferencePending[0],true);
 assert.equal(restored.state.preferencePending.length,0);assert.match(r.receipt,/本地也已读取并应用/);
 delete globalThis[KEY];
});
test('unsupported result replaces a model promise and stale snapshots do not overwrite current values',async()=>{
 const s=fixture();s.transport={request:async()=>({status:'unsupported',receipt:'暂不支持自动打电话。',snapshot:{revision:4,values:{short_limit:50}}})};
 await s.preferenceBridge.before([event('以后每天打电话')]);
 assert.equal(s.lastReceipt.text,'暂不支持自动打电话。');
 assert.equal(shortBeats([{text:'以后每天我都会打给你',actions:[],anchors:[]}])[0].text,'暂不支持自动打电话。');
 s.acceptPreferences({revision:3,values:{short_limit:10}});assert.equal(s.preferenceBridge.values.short_limit,50);
 s.onTurnEnded();assert.match(shortBeats([{text:'偏好已保存',actions:[],anchors:[]}])[0].text,/没有取得保存回执/);
 delete globalThis[KEY];
});
