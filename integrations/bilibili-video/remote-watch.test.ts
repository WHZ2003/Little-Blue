import test from 'node:test';
import assert from 'node:assert/strict';
import { RemoteWatch } from './remote-watch.ts';
const URL='https://www.bilibili.com/video/BV1LHHw6pERT/?p=1';
function fixture(options:any={}){
  let clock=0,closed=0,opened=0,offset=0;
  const owner={eligible:true};let ticket={owner,url:URL};
  const b:any={freshOwnedPage:async()=>{opened++;},goto:async()=>{},ownershipTicket:()=>ticket,
    ticketMatches:(t:any)=>t===ticket&&!closed,waitForVideo:async()=>!options.failOpen,
    videoInfo:async()=>({bvid:'BV1LHHw6pERT',p:1,title:'测试视频',duration:3}),play:async()=>{},seek:async()=>{options.resume=false;options.delayedResume=false;offset=clock;},
    playback:async()=>({t:options.resume?2.5:options.delayedResume&&clock>=1000?3:options.seek?3:Math.min((clock-offset)/1000,3),duration:3,ended:options.seek||clock-offset>=3000}),
    sample:async()=>({jpeg:'real-frame-placeholder'}),finishOwnedVideo:async()=>{closed++;return 'closed';}};
  const w:any={host:{},mode:'idle',browser:b,stopObserving:()=>{},resetVideo:()=>{}};
  const reports:any[]=[];
  (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]={values:{auto_close_video_tabs:true}};
  const remote=new RemoteWatch(w,async ms=>{clock+=ms;if(options.takeover&&clock>=500)owner.eligible=false;if(options.rewrite&&clock===500)ticket={owner,url:URL+'&tracking=changed'};},()=>clock);
  return {remote,reports,w,owner,get closed(){return closed;},get opened(){return opened;}};
}
test('verified playback emits started then completed; only owned tab cleaned',async()=>{
  const f=fixture();await f.remote.start({id:'test',url:URL},r=>f.reports.push(r));
  assert.deepEqual(f.reports.map(r=>r.state),['playing','completed']);
  assert.ok(f.reports[1].played>=2.7);assert.equal(f.closed,1);assert.equal(f.w.mode,'idle');
});
test('failure and seek-to-end never become completed',async()=>{
  for(const opt of [{failOpen:true},{seek:true}]){
    const f=fixture(opt);await f.remote.start({id:'test',url:URL},r=>f.reports.push(r));
    assert.equal(f.reports.at(-1).state,'failed');assert.equal(f.closed,1);
  }
});
test('user takeover preserves their page and fails honestly',async()=>{
  const f=fixture({takeover:true});await f.remote.start({id:'test',url:URL},r=>f.reports.push(r));
  assert.equal(f.reports.at(-1).reason,'taken_over');assert.equal(f.closed,0);
});
test('site tracking-URL rewrite preserves the same owned video playback',async()=>{
  const f=fixture({rewrite:true});await f.remote.start({id:'test',url:URL},r=>f.reports.push(r));
  assert.equal(f.reports.at(-1).state,'completed');assert.equal(f.closed,1);
});
test('account resume position is reset during startup including after first progress',async()=>{
  for(const options of [{resume:true},{delayedResume:true}]){
    const f=fixture(options);await f.remote.start({id:'test',url:URL},r=>f.reports.push(r));
    assert.equal(f.reports.at(-1).state,'completed');assert.ok(f.reports.at(-1).played>=2.7);
    assert.equal(f.reports.filter(r=>r.state==='playing').length,1);
  }
});
test('expired lease and arbitrary URLs cannot open',async()=>{
  const f=fixture();await f.remote.start({id:'test',url:'https://example.org'},r=>f.reports.push(r));
  assert.equal(f.opened,0);
  await f.remote.start({id:'test',url:URL,permitted:()=>false},r=>f.reports.push(r));
  assert.equal(f.opened,0);assert.equal(f.reports.at(-1).reason,'disconnected');
});
