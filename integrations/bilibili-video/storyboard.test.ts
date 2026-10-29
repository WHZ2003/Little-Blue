import test from 'node:test';
import assert from 'node:assert/strict';
import { BilibiliVideoWorld } from './world.ts';
import { BILIBILI_VIDEO_DEFAULTS } from './config.ts';

function fixture() {
  const cfg=structuredClone(BILIBILI_VIDEO_DEFAULTS);
  const world:any=new BilibiliVideoWorld({cfg,timezone:'UTC',dataDir:'.cache/storyboard-test'});
  world.mode='watching';world.video={title:'synthetic'};
  world.subtitles=[{from:0,to:20,content:'timeline subtitle'}];
  world.samples=Array.from({length:40},(_,i)=>({t:(i+1)/2,w:640,h:360,jpeg:'frame',hash:(BigInt(i)*0x123456789abcdefn).toString(16).padStart(16,'0').slice(-16),diff:i%3===0?60:0,luma:120}));
  const calls:any[]=[];
  world.browser.montage=async(...args:any[])=>{calls.push(args);return Buffer.from('jpeg').toString('base64');};
  // Do not schedule real timers for unit observations.
  world.armObservation=()=>{};
  return {world,calls};
}

test('real world renderer emits one bounded storyboard with timeline subtitles and advances evidence',async()=>{
  const {world,calls}=fixture();
  const result=await world.renderScene();
  assert.equal(result.blobs.length,1);assert.match(result.text,/cell 1/);assert.match(result.text,/timeline subtitle/);
  const [,cols,rows,w,h,,numbered]=calls[0];
  assert.ok(cols*rows*w*h<=640000);assert.equal(numbered,true);
  assert.equal(world.lastObservedT,20);assert.equal(world.sceneCount,1);
  world.stopObserving();
});

test('in-flight montage cannot deliver after stop/restart or switch videos',async()=>{
  const {world}=fixture();let finish:any;
  world.browser.montage=()=>new Promise(r=>finish=r);
  const render=world.renderScene();world.stopObserving();world.mode='watching';
  finish(Buffer.from('late').toString('base64'));
  assert.equal(await render,null);assert.equal(world.sceneCount,0);
});

test('in-flight capture from a stopped generation cannot enter next video',async()=>{
  const {world}=fixture();let finish:any;world.samples=[];
  world.browser.sample=()=>new Promise(r=>finish=r);
  const capture=world.capture();world.stopObserving();
  finish({t:900,jpeg:'stale',w:640,h:360,gray:Array(64*36).fill(100)});
  await capture;assert.equal(world.samples.length,0);
});

test('switching off storyboard uses original observation path',async()=>{
  const {world,calls}=fixture();world.cfg.observe.storyboard=false;world.video.chapters=[];
  const result=await world.renderScene();
  assert.match(result.blobs[0].name,/scene-/);
  assert.equal(calls[0][6],undefined);
});

test('remote takeover while a grid is being composed drops the picture',async()=>{
  const {world}=fixture();let finish:any;
  const owner={eligible:true};world.remote.active=true;
  world.browser.ownershipTicket=()=>({owner});
  world.browser.montage=()=>new Promise(r=>finish=r);
  const render=world.renderScene();owner.eligible=false;
  finish(Buffer.from('late').toString('base64'));
  assert.equal(await render,null);assert.equal(world.sceneCount,0);
});
