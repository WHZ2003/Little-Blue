import assert from 'node:assert/strict';
import test from 'node:test';
import { OwnedVideoPage } from './owned-page.ts';
import { VideoBrowser } from './browser.ts';
import { BilibiliVideoWorld } from './world.ts';
import { BILIBILI_VIDEO_DEFAULTS } from './config.ts';

function fakePage() {
  const page = { closed: false, closes: 0, address: 'https://www.bilibili.com/video/BV1234567890', touched: false,
    url() { return this.address; }, isClosed() { return this.closed; },
    async evaluate() { return !this.touched; },
    async close() { this.closes++; this.closed = true; } };
  return page;
}
test('only the created page closes; unrelated page and browser survive', async () => {
  const mine=fakePage(), user=fakePage();
  const owner=new OwnedVideoPage(mine as any);
  assert.equal(await owner.close(), 'closed');
  assert.equal(mine.closes,1); assert.equal(user.closes,0);
  assert.equal(await owner.close(), 'retained');
});
test('operator input, navigation, stale tickets and failed probes retain pages', async () => {
  for (const action of ['release','navigation','url','touched','probe']) {
    const page=fakePage(), owner=new OwnedVideoPage(page as any), ticket=owner.ticket();
    if(action==='release')owner.revoke();
    if(action==='navigation')owner.navigated(false);
    if(action==='url')page.address='https://example.org/user-work';
    if(action==='touched')page.touched=true;
    if(action==='probe')page.evaluate=async()=>{throw Error('timeout');};
    assert.equal(await owner.close(ticket),'retained',action); assert.equal(page.closes,0);
  }
});
test('takeover while renderer probe is pending wins', async () => {
  const page=fakePage(), owner=new OwnedVideoPage(page as any);
  let resolve!: (value:boolean)=>void;
  page.evaluate=()=>new Promise<boolean>(r=>resolve=r);
  const close=owner.close(); owner.revoke(); resolve(true);
  assert.equal(await close,'retained'); assert.equal(page.closes,0);
});
test('close error is not reported as success; duplicate finish closes once', async () => {
  const page=fakePage(), owner=new OwnedVideoPage(page as any);
  page.close=async()=>{page.closes++;throw Error('closed denied');};
  assert.equal(await owner.close(),'failed'); assert.equal(page.closed,false);
  page.close=async()=>{page.closes++;page.closed=true;};
  await Promise.all([owner.close(),owner.close()]); assert.equal(page.closes,2);
});
test('browser stop disconnects and never closes the browser or kills a process', async () => {
  const page=fakePage(), browser:any=new VideoBrowser({cfg:BILIBILI_VIDEO_DEFAULTS, dataDir:'.',jar:{load:()=>[]},log:()=>null} as any);
  let disconnects=0;
  browser.browser={disconnect:async()=>{disconnects++;},close:()=>assert.fail('browser.close')};
  browser.process={kill:()=>assert.fail('process.kill'),unref:()=>{}};
  browser.page=page;browser.owned=new OwnedVideoPage(page as any);
  browser.owned.revoke();
  await browser.stop();assert.equal(page.closes,0);assert.equal(disconnects,1);
});
function world() {
  const instance:any=new BilibiliVideoWorld({cfg:structuredClone(BILIBILI_VIDEO_DEFAULTS),timezone:'UTC',dataDir:'.'});
  const events:string[]=[];
  instance.host={pushEvent:async(e:any)=>events.push(e.text)};
  instance.mode='watching'; instance.video={title:'Test'};
  const page=fakePage(), owned=new OwnedVideoPage(page as any);
  instance.browser={ownershipTicket:()=>owned.ticket(),ticketMatches:(t:any)=>owned.matches(t),
    playback:async()=>({ended:true,duration:5,t:5,paused:true}),
    finishOwnedVideo:(t:any)=>owned.close(t),pause:async()=>{},setWebFullscreen:async()=>{}};
  (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]={values:{auto_close_video_tabs:true}};
  return {instance,page,owned,events};
}
test('real world end event cleans up, pause retains, opt-out retains, exit cleans up',async()=>{
  const ended=world(); await ended.instance.poll(); assert.equal(ended.page.closes,1);assert.equal(ended.instance.mode,'idle');
  assert.match(ended.events[0],/已自动关闭/);
  const paused=world();paused.instance.browser.playback=async()=>({ended:false,paused:true,duration:5,t:2});
  await paused.instance.poll();assert.equal(paused.page.closes,0);
  const disabled=world();(globalThis as any)[Symbol.for('dafeiyu.shared-preferences')].values.auto_close_video_tabs=false;
  await disabled.instance.poll();assert.equal(disabled.page.closes,0);
  const exit=world();await exit.instance.exit();assert.equal(exit.page.closes,1);
});
test('stale playback result cannot close a navigated page',async()=>{
  const w=world();let resolve!: (value:any)=>void;
  w.instance.browser.playback=()=>new Promise(r=>resolve=r);
  const pending=w.instance.poll();w.owned.navigated(false);resolve({ended:true,duration:5});
  await pending;assert.equal(w.page.closes,0);
});
