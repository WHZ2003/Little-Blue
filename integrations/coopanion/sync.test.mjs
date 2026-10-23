import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';import {join} from 'node:path';
import {SharedMemory,exportEvent,digest} from './index.mjs';

test('exports only real chat and never loops imported history',()=>{
 const e={cursor:4,ts:'2026-10-22T20:00:00Z',source:'desktop-pet',type:'desktop-pet.message',meta:{text:'hello'}};
 assert.equal(exportEvent(e).text,'hello');assert.equal(exportEvent({...e,meta:{...e.meta,dafeiyuCloudId:1}}),null);
 assert.equal(exportEvent({...e,type:'desktop-pet.touch'}),null);
});
test('offline replay, no history wakeup, and original persona survives',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'coop-sync-test-')),workspace=join(dir,'workspace');mkdirSync(workspace);
 writeFileSync(join(workspace,'CONSTITUTION.md'),'unchanged');writeFileSync(join(dir,'persona.md'),'unchanged');
 const s=new SharedMemory({botDir:dir,dataDir:join(dir,'data'),cfg:{personaSnapshot:join(dir,'persona.md')}});
 s.stopped=false;
 const events=[{cursor:1,source:'desktop-pet',type:'desktop-pet.message',ts:'2026-10-22T20:00:00Z',meta:{text:'local'}}];
 let imports=0,notice;
 s.host={store:{latestCursor:()=>events.at(-1).cursor,range:q=>events.filter(e=>e.cursor>=(q.fromCursor??0)&&e.cursor<=(q.toCursor??999))},
 pushEvent:async(e,opts)=>{assert.equal(opts.deliver,false);imports++;const row={...e,cursor:events.length+1};events.push(row);return row;},
 pushDeferred:(e,opts)=>{assert.equal(opts.trigger,'piggyback');notice=e;}};
 s.transport={request:async()=>{throw new Error('offline');}};
 await s.sync();assert.equal(s.state.localCursor,0);assert.equal(imports,0);
 const row={id:1,role:'assistant',origin:'wechat',text:'cloud',created:1000,delivery:'delivered'};
 s.transport={request:async p=>({accepted:p.events.map(e=>e.key),files:{},persona_sha256:digest('unchanged'),conversation:[row],tail:[row],memories:[],memory_status:{},diaries:[],counts:{conversation:1,memories:0,diaries:0},after:1,memory_after:0,more:false,wechat:{state:'connected'}})};
 await s.sync();await s.sync();assert.equal(imports,1);assert.equal(readFileSync(join(workspace,'CONSTITUTION.md'),'utf8'),'unchanged');
 assert.match(notice.render().text,/cloud/);assert.equal(s.state.localCursor,2);
 assert.equal(JSON.parse(readFileSync(join(workspace,'shared/archive.json'),'utf8')).conversation.length,1);
});
