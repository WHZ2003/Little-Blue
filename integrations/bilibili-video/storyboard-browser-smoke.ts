import assert from 'node:assert/strict';
import { mkdtempSync, existsSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import puppeteer from 'puppeteer-core';
import { VideoBrowser } from './browser.ts';
import { BILIBILI_VIDEO_DEFAULTS } from './config.ts';
import { canvasPlan } from './storyboard/live.mjs';

const executable=['C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(existsSync);
assert.ok(executable);
const profile=mkdtempSync(resolve('.cache/storyboard-browser-'));
const chrome=await puppeteer.launch({executablePath:executable,headless:true,userDataDir:profile});
try {
  const cfg=structuredClone(BILIBILI_VIDEO_DEFAULTS);
  cfg.browser.cdpUrl='http://'+new URL(chrome.wsEndpoint()).host;
  const video=new VideoBrowser({cfg,dataDir:profile,jar:{load:()=>[]},log:()=>null,onNavigated:()=>{},onLost:()=>{},onDanmaku:()=>{},onSubtitleTracks:()=>{}} as any);
  const page=await video.ensure();
  const cells=await page.evaluate(()=>Array.from({length:9},(_,i)=>{
    const c=document.createElement('canvas');c.width=640;c.height=360;
    const ctx=c.getContext('2d')!;ctx.fillStyle=`hsl(${i*40},70%,45%)`;ctx.fillRect(0,0,640,360);
    ctx.fillStyle='#fff';ctx.font='42px sans-serif';ctx.fillText(`Sample ${i+1}`,160,180);
    return c.toDataURL('image/jpeg').split(',')[1];
  }));
  const grid=canvasPlan(9,640,360);
  const jpeg=await video.montage(cells,grid.cols,grid.rows,grid.cellW,grid.cellH,.8,true);
  assert.ok(jpeg);
  const actual=await page.evaluate(async src=>{
    const im=new Image();im.src='data:image/jpeg;base64,'+src;await im.decode();return {width:im.width,height:im.height};
  },jpeg);
  assert.deepEqual(actual,{width:grid.width,height:grid.height});
  assert.ok(actual.width*actual.height<=640000);
  assert.equal(await video.montage(['invalid'],1,1,300,200,.8,true),null,'failed source must not masquerade as a real frame');
  const output=resolve('.cache/storyboard-preview.jpg');writeFileSync(output,Buffer.from(jpeg,'base64'));
  console.log(JSON.stringify({storyboard_browser:'passed',...actual,output}));
  await video.stop();
} finally {await chrome.close();}
