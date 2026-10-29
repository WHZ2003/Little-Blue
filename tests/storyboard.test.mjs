import test from 'node:test';
import assert from 'node:assert/strict';
import { canvasPlan, selectStoryboard, storyboardManifest, sameNativePicture, observationInterval, PIXEL_BUDGET } from '../integrations/storyboard/live.mjs';

test('budget applies to full grid for landscape/portrait/single latest frame, never per cell', () => {
  for (const [w, h] of [[1920,1080], [1080,1920], [2560,1080], [800,800]]) {
    for (let n=1;n<=9;n++) {
      const p=canvasPlan(n,w,h);
      assert.ok(p.cols*p.rows>=n);
      assert.ok(p.width*p.height<=PIXEL_BUDGET);
      assert.ok(Math.abs(p.cellW/p.cellH-w/h)<.02);
    }
  }
  assert.equal(observationInterval({}),20);
  assert.equal(observationInterval({minIntervalSec:40}),40);
  assert.equal(observationInterval({storyboard:false,minIntervalSec:5}),5);
  assert.equal(canvasPlan(1,640,360).cellW,640,'never upscale a single live sample');
});

test('selection keeps newest, deduplicates freezes, stays within retained live samples', () => {
  const frames=Array.from({length:180},(_,i)=>({t:100+i*.5,diff:i>110&&i<135?60:0,hash:i.toString(16).padStart(16,'0')}));
  const cells=selectStoryboard(frames,0,{maxFrames:9,dedupeHamming:0});
  assert.ok(cells.length<=9);assert.equal(cells.at(-1).index,179);
  assert.equal(cells[0].from,100);assert.equal(cells.at(-1).to,189.5);
  cells.forEach((c,i)=>{assert.ok(c.to>=c.from);if(i)assert.equal(c.from,cells[i-1].to);});
  const freeze=frames.map(f=>({...f,hash:'0123456789abcdef'}));
  assert.deepEqual(selectStoryboard(freeze,0).map(c=>c.index),[179]);
  assert.deepEqual(selectStoryboard([],0),[]);
  assert.equal(selectStoryboard([frames[0]],0).length,1);
});

test('manifest aligns transcript to cells, bounds text, preserves actual sample time', () => {
  const frames=[{t:1},{t:9}];
  const cells=[{index:0,from:0,to:5},{index:1,from:5,to:10}];
  const text=storyboardManifest(cells,frames,[{from:0,to:4,content:'first'},{from:5,to:8,content:'second'},{from:10,to:12,content:'outside'.repeat(1000)}],canvasPlan(2,1920,1080));
  assert.match(text,/cell 1.*取帧 00:01.00.*first/);
  assert.match(text,/cell 2.*取帧 00:09.00（最新）.*second/);
  assert.ok(text.length<900);
  assert.doesNotMatch(text.split('\n')[1],/second/);
});

test('native encoder noise can be skipped, hand changes and missing maps cannot', () => {
  const a=new Uint8Array(128*72).fill(100),b=a.slice();b[0]++;
  assert.equal(sameNativePicture(a,b),true);
  b.fill(180,128*60,128*61);
  assert.equal(sameNativePicture(a,b),false);
  assert.equal(sameNativePicture(undefined,b),false);
});
