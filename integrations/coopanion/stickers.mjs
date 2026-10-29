import { readFileSync, realpathSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';

// Only registered, integrity-checked local assets may become chat attachments.
export class Stickers {
  constructor(root) {
    this.root = root; this.items = [];
    try { this.items = JSON.parse(readFileSync(join(root,'nekowhale.json'),'utf8')).items; } catch {}
  }
  search(query) {
    return this.list(query,1).items;
  }
  list(query='',page=1) {
    if(typeof query!=='string'||query.length>120||!Number.isInteger(page)||page<1||page>1000)throw Error('表情搜索参数无效');
    const terms=String(query||'').trim().split(/\s+/).filter(Boolean);
    const ranked=this.items.map(i=>({i,score:[i.id,i.title,i.emotion,...i.tags].reduce((n,w)=>n+terms.reduce((v,t)=>v+(w.includes(t)||t.includes(w)?w.length:0),0),0)}))
      .filter(x=>!terms.length||x.score).sort((a,b)=>b.score-a.score);
    const pages=Math.max(1,Math.ceil(ranked.length/12));page=Math.min(page,pages);
    const items=ranked.slice((page-1)*12,page*12)
      .map(({i})=>({id:i.id,title:i.title,emotion:i.emotion,context:i.auto_context,direction:i.direction}));
    return {items,total:ranked.length,installed:this.items.length,page,pages};
  }
  panel(method,args=[]) {
    if(!Array.isArray(args)||args.length!==1||!args[0]||typeof args[0]!=='object')throw Error('表情参数无效');
    if(method==='list')return this.list(args[0].query,args[0].page);
    if(method==='preview'){
      const blob=this.get(args[0].id).blobs[0];
      return {src:'data:image/gif;base64,'+blob.bytes.toString('base64')};
    }
    throw Error('未知表情操作');
  }
  get(id) {
    const item=this.items.find(i=>i.id===id);
    if(!item||!/^n\d{3}$/.test(id))throw Error('未知表情编号，请先搜索');
    const path=resolve(this.root,'nekowhale',id+'.gif');
    if(realpathSync(path)!==path||statSync(path).size>20*1024*1024)throw Error('表情路径或大小无效');
    const bytes=readFileSync(path);
    const sha=createHash('sha1').update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest('hex');
    if(bytes.length!==item.bytes||sha!==item.git_blob_sha||!['GIF87a','GIF89a'].includes(bytes.subarray(0,6).toString()))throw Error('表情校验失败');
    return {text:`表情「${item.title}」已读取。请用 pet_say 的 images 参数传入本次返回的 blob handle 才会显示在聊天页；不要把路径或编号当作 handle。作者：可以叫我云大人/雾雪 https://kiriyuki.com https://space.bilibili.com/103739`,
      blobs:[{bytes,mime:'image/gif',name:id+'.gif',fallbackText:item.title+'；'+item.auto_context}]};
  }
  tools() { return [
    {name:'sticker_search',description:'按名称、情绪或场景搜索已安装的大肥鱼动图，每次最多12张。普通接话偶尔配图，尊重用户不发图的要求。',parameters:{type:'object',properties:{query:{type:'string',maxLength:120}},required:['query'],additionalProperties:false},tags:['read'],handler:async a=>JSON.stringify(this.search(a.query))},
    {name:'sticker_get',description:'读取搜索结果中的表情编号，返回 GIF blob；随后用 pet_say 的 images 显示。只在本地聊天展示，不发送微信。',parameters:{type:'object',properties:{id:{type:'string',pattern:'^n[0-9]{3}$'}},required:['id'],additionalProperties:false},tags:['read'],handler:async a=>{try{return this.get(a.id);}catch{return {text:'表情暂不可用，请重新搜索或继续文字聊天。',failed:true};}}}
  ]; }
}
