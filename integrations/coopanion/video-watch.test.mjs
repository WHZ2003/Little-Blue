import test from 'node:test';
import assert from 'node:assert/strict';
import { VideoRelay,VIDEO_KEY } from './video-watch.mjs';
const flush=()=>new Promise(r=>setImmediate(r));
test('offered job requires server claim ack; same ack never opens twice',async()=>{
  const shared={state:{},save(){}};const r=new VideoRelay(shared);let starts=0;
  globalThis[VIDEO_KEY]={ready:()=>true,start:async(job,report)=>{starts++;report({state:'playing',position:1,duration:3});report({state:'completed',position:3,duration:3,played:3});}};
  const j={id:'a'.repeat(32),token:'b'.repeat(32),session:r.session,url:'https://www.bilibili.com/video/BV1LHHw6pERT/?p=1',lease:145};
  r.accept({job:j,claimed:null,accepted:[],active:[j.id],server_time:101});await flush();assert.equal(starts,0);
  assert.equal(r.payload().claim.id,j.id);
  r.accept({job:null,claimed:j.id,accepted:[],active:[j.id],server_time:102});
  r.accept({job:null,claimed:j.id,accepted:[],active:[j.id],server_time:103});await flush();assert.equal(starts,1);
  assert.equal(shared.state.videoReports.length,2);
  r.accept({job:null,claimed:null,accepted:[j.id+':playing',j.id+':completed'],active:[],server_time:104});
  assert.equal(shared.state.videoReports.length,0);
});
test('restart never replays old physical work; forged URLs never become pending',()=>{
  const shared={state:{videoWork:{id:'a'.repeat(32),token:'b'.repeat(32),session:'c'.repeat(32),phase:'started'}},save(){}};
  const r=new VideoRelay(shared);
  assert.equal(shared.state.videoWork,undefined);assert.equal(shared.state.videoReports[0].reason,'restarted');
  r.accept({job:{id:'d'.repeat(32),token:'e'.repeat(32),session:r.session,url:'file:///private',lease:100},accepted:[],active:[],server_time:1});
  assert.equal(shared.state.videoWork,undefined);
});
