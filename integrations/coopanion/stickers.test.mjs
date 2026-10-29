import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { Stickers } from './stickers.mjs';

test('registered GIF search, attachment and tamper rejection',()=>{
  const root=mkdtempSync(join(tmpdir(),'nekowhale-'));
  try {
    mkdirSync(join(root,'nekowhale'));
    const bytes=Buffer.from('GIF89a synthetic test image');
    const item={id:'n103',title:'摸鱼',emotion:'开心',tags:['休息'],auto_context:'闲聊休息',bytes:bytes.length,git_blob_sha:createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex')};
    writeFileSync(join(root,'nekowhale.json'),JSON.stringify({items:[item]}));
    const path=join(root,'nekowhale','n103.gif');writeFileSync(path,bytes);
    const pack=new Stickers(root);
    assert.equal(pack.search('想摸鱼')[0].id,'n103');
    assert.equal(pack.get('n103').blobs[0].mime,'image/gif');
    assert.match(pack.panel('preview',[{id:'n103'}]).src,/^data:image\/gif;base64,/);
    assert.throws(()=>pack.panel('preview',[{id:'../secret'}]));
    assert.throws(()=>pack.get('../secret'));
    writeFileSync(path,Buffer.from('GIF89a tampered'));
    assert.throws(()=>pack.get('n103'));
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('UI listing pages all registered stickers and bounds searches',()=>{
  const pack=new Stickers('/missing-nekowhale-fixture');
  pack.items=Array.from({length:322},(_,n)=>({id:'n'+String(n+1).padStart(3,'0'),title:'表情'+(n+1),emotion:'开心',tags:['休息'],auto_context:'休息时'}));
  const first=pack.panel('list',[{query:'',page:1}]);
  assert.equal(first.total,322);assert.equal(first.pages,27);assert.equal(first.items.length,12);
  assert.equal(pack.list('',27).items.length,10);
  assert.equal(pack.list('',99).page,27);
  assert.equal(pack.list('n103',1).items[0].id,'n103');
  assert.equal(pack.list('不存在',1).total,0);
  assert.throws(()=>pack.list('',0));assert.throws(()=>pack.list('a'.repeat(121),1));
  assert.throws(()=>pack.panel('send',[{id:'n103'}]));
});
