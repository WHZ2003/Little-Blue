import test from 'node:test';import assert from 'node:assert/strict';
import {shortBeats,validQuestion} from './chat-format.mjs';

test('long speech stays whole and preserves action positions',()=>{
 const input={text:'长'.repeat(65),actions:['wave'],anchors:[{at:35,actions:['smile']}]};
 const parts=shortBeats([input]);
 assert.deepEqual(parts.map(p=>p.text.length),[65]);
 assert.equal(parts.map(p=>p.text).join(''),input.text);
 assert.deepEqual(parts[0].actions,['wave']);
 assert.deepEqual(parts[0].anchors,[{at:35,actions:['smile']}]);
});
test('single-line output removes the tic but keeps real words and Unicode',()=>{
 for(const text of ['你好，库...','第一句\n第二句','🙂'.repeat(30),'数据库和仓库都在。']){
  const parts=shortBeats([{text,actions:[],anchors:[]}]);
  assert(parts.every(p=>Array.from(p.text).length<=30&&!/[\r\n]/u.test(p.text)));
  if(text==='你好，库...')assert.equal(parts[0].text,'你好');
  if(text==='数据库和仓库都在。')assert.equal(parts[0].text,text);
 }
 assert(!validQuestion('长'.repeat(31)));assert(!validQuestion('你\n好'));
 assert(validQuestion('用哪个数据库？'));
});
