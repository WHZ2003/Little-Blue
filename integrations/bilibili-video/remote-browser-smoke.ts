import assert from 'node:assert/strict';
import { existsSync,mkdtempSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import puppeteer from 'puppeteer-core';
import { BilibiliVideoWorld } from './world.ts';
import { BILIBILI_VIDEO_DEFAULTS } from './config.ts';
import { VIDEO_KEY } from './remote-watch.ts';
const {VideoRelay}=await import(pathToFileURL(resolve('integrations/coopanion/video-watch.mjs')).href);
const executable=['C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(existsSync)!;
const folder=mkdtempSync(resolve('.cache/remote-video-smoke-'));
const fixture=await puppeteer.launch({executablePath:executable,headless:true,userDataDir:folder,args:['--autoplay-policy=no-user-gesture-required']});
try{
  const user=await fixture.newPage();await user.setContent('<input value="user work">');
  const maker=await fixture.newPage();
  const data=await maker.evaluate(`(async()=>{
    const c=document.createElement('canvas');c.width=320;c.height=180;
    const ctx=c.getContext('2d');const stream=c.captureStream(20);
    const rec=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks=[];
    rec.ondataavailable=e=>chunks.push(e.data);
    const done=new Promise(resolve=>rec.onstop=resolve);
    let n=0;const timer=setInterval(()=>{ctx.fillStyle=n++%2?'#2090ff':'#ff9020';ctx.fillRect(0,0,320,180);},40);
    rec.start();await new Promise(r=>setTimeout(r,2400));rec.stop();await done;clearInterval(timer);stream.getTracks().forEach(t=>t.stop());
    return await new Promise(resolve=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.readAsDataURL(new Blob(chunks,{type:'video/webm'}));});
  })()`);
  const cfg=structuredClone(BILIBILI_VIDEO_DEFAULTS);cfg.browser.cdpUrl='http://'+new URL(fixture.wsEndpoint()).host;
  const scenes:any[]=[];
  const world:any=new BilibiliVideoWorld({cfg,timezone:'UTC',dataDir:folder});world.host={pushEvent:async()=>{},pushDeferred:(event:any)=>{void Promise.resolve(event.render()).then(value=>{if(value)scenes.push(value);});}};
  const actual=world.browser.freshOwnedPage.bind(world.browser);
  let created:any=null;
  world.browser.freshOwnedPage=async()=>{
    created=await actual();await created.setRequestInterception(true);
    created.on('request',(r:any)=>{
      if(r.isNavigationRequest())void r.respond({status:200,contentType:'text/html',body:`<video controls src="${data}"></video><script>window.__INITIAL_STATE__={p:1,videoData:{bvid:'BV1LHHw6pERT',title:'本地生成的测试片段',duration:2.4,pages:[{page:1,cid:1,duration:2.4}]}};setTimeout(()=>history.replaceState({},'',location.pathname+'?p=1&tracking=site-rewrite'),350);</script>`});
      else void r.abort();
    });return created;
  };
  globalThis[VIDEO_KEY as any]=world.remote;
  (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]={values:{auto_close_video_tabs:true}};
  const shared:any={state:{},save(){}};const relay=new VideoRelay(shared);
  const job={id:'a'.repeat(32),token:'b'.repeat(32),session:relay.session,url:'https://www.bilibili.com/video/BV1LHHw6pERT/?p=1',lease:Date.now()/1000+45};
  relay.accept({job,accepted:[],active:[job.id],server_time:Date.now()/1000});assert.equal(created,null);
  relay.accept({job:null,claimed:job.id,accepted:[],active:[job.id],server_time:Date.now()/1000});
  const deadline=Date.now()+20000;
  while(!shared.state.videoReports.some((r:any)=>r.state!=='playing')&&Date.now()<deadline)await new Promise(r=>setTimeout(r,100));
  const ended=shared.state.videoReports.find((r:any)=>r.state!=='playing');
  assert.equal(ended?.state,'completed',JSON.stringify(shared.state.videoReports));
  assert.ok(ended.played>=ended.duration*.9);assert.ok(created.isClosed());assert.ok(!user.isClosed()&&!maker.isClosed());
  assert.ok(scenes.some(s=>s.blobs?.length===1&&s.text.includes('分镜清单')),'remote playback must deliver a real storyboard, including short clips');
  assert.ok(ended.frames>=1,'receipt must count actual observed storyboard frames');
  const first=ended;
  // A second real playback transfers to the human on a trusted click; never clean that page.
  const reports:any[]=[];const watching=world.remote.start({id:'second',url:job.url},(r:any)=>reports.push(r));
  while(!reports.some(r=>r.state==='playing')){assert.ok(Date.now()<deadline);await new Promise(r=>setTimeout(r,100));}
  await created.click('video');await watching;
  assert.equal(reports.at(-1).state,'failed');assert.equal(reports.at(-1).reason,'taken_over');
  assert.ok(!created.isClosed()&&!user.isClosed());
  await world.browser.stop();assert.ok(!created.isClosed()&&!user.isClosed());
  console.log(JSON.stringify({real_playback:'passed',duration:first.duration,played:first.played,frames:first.frames,storyboards:scenes.length,
    checks:['claim-before-open','actual-media-playback','started-before-completed','owned-tab-cleanup','user-tab-preserved','trusted-user-takeover-preserved']}));
}finally{await fixture.close();}
