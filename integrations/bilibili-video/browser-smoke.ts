// Real Chromium, isolated temporary profile, no live user browser and no outside requests.
import assert from 'node:assert/strict';
import { mkdtempSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import puppeteer from 'puppeteer-core';
import { VideoBrowser } from './browser.ts';
import { BILIBILI_VIDEO_DEFAULTS } from './config.ts';
import { BilibiliVideoWorld } from './world.ts';

const executable=['C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(existsSync);
assert.ok(executable);
const profile=mkdtempSync(resolve('.cache/video-ownership-browser-'));
const fixture=await puppeteer.launch({executablePath:executable,headless:true,userDataDir:profile,args:['--no-first-run']});
try {
  const user=await fixture.newPage(); await user.setContent('<input value="user work">');
  const config=structuredClone(BILIBILI_VIDEO_DEFAULTS);
  const endpoint=new URL(fixture.wsEndpoint());config.browser.cdpUrl=`http://${endpoint.host}`;
  const options:any={cfg:config,dataDir:profile,jar:{load:()=>[]},log:()=>null,onNavigated:()=>{},onLost:()=>{},onDanmaku:()=>{},onSubtitleTracks:()=>{}};
  const managed=new VideoBrowser(options);
  const mine=await managed.ensure();
  assert.notEqual(mine.target()._targetId,user.target()._targetId);
  const laterUser=await fixture.newPage();await laterUser.setContent('<p>new user tab</p>');
  await mine.setContent('<video></video>');
  await mine.evaluate("const v=document.querySelector('video');Object.defineProperty(v,'ended',{get:()=>true});Object.defineProperty(v,'duration',{get:()=>10});");
  const world:any=new BilibiliVideoWorld({cfg:config,timezone:'UTC',dataDir:profile});
  world.browser=managed;world.mode='watching';world.video={title:'Synthetic video'};
  const events:string[]=[];world.host={pushEvent:async(e:any)=>events.push(e.text)};
  (globalThis as any)[Symbol.for('dafeiyu.shared-preferences')]={values:{auto_close_video_tabs:true}};
  await world.poll();
  assert.ok(mine.isClosed());assert.ok(!user.isClosed()&&!laterUser.isClosed());assert.match(events[0],/已自动关闭/);
  await managed.goto('data:text/html,<input id="work">');
  const taken=await managed.ensure();
  await taken.click('#work');
  assert.equal(await managed.finishOwnedVideo(managed.ownershipTicket()),'retained');
  await managed.goto('about:blank');
  const replacement=await managed.ensure();assert.notEqual(replacement,taken);
  assert.ok(!taken.isClosed());
  await managed.stop();assert.ok(replacement.isClosed());assert.ok(!taken.isClosed()&&!user.isClosed()&&!laterUser.isClosed());
  const restarted=new VideoBrowser(options);const fresh=await restarted.ensure();
  assert.ok(!taken.isClosed()&&!user.isClosed());
  await restarted.stop();assert.ok(fresh.isClosed());
  console.log(JSON.stringify({real_browser:'passed',checks:['preexisting tabs preserved','later user tabs preserved','actual ended poll closes owned tab','trusted input transfers ownership','next navigation creates fresh tab','shutdown preserves user tabs','restart never adopts existing tabs']}));
} finally {await fixture.close();}
