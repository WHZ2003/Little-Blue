export function mountStickers(ctx) {
  const {root,ui,signal}=ctx,doc=root.ownerDocument;
  const make=(tag,text)=>{const el=doc.createElement(tag);if(text)el.textContent=text;return el;};
  const form=make('form');form.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin:18px 0';
  const query=make('input');query.type='search';query.maxLength=120;query.placeholder='搜索名称、情绪或编号，例如：摸鱼';query.setAttribute('aria-label','搜索表情');
  query.style.cssText='flex:1;min-width:180px;padding:10px;border:1px solid #a1a1aa;border-radius:8px;background:transparent;color:inherit';
  const search=ui.button('搜索',{onClick:()=>void load(1)});form.append(query,search);
  const status=ui.msgline(),grid=make('div');grid.style.cssText='display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px';
  const selected=make('section');selected.hidden=true;selected.style.cssText='margin:20px 0;padding:16px;border:1px solid #a1a1aa;border-radius:12px';
  const title=make('h3'),picture=make('img');picture.style.cssText='display:block;width:220px;max-width:100%;height:220px;object-fit:contain;background:#f1f5f9;border-radius:10px;margin:12px 0';
  const description=make('p'),receipt=ui.msgline(),actions=ui.actions();
  let chosen=null,page=1,pages=1,busy=false,previewVersion=0;
  async function copy(text){try{await navigator.clipboard.writeText(text);receipt.textContent='已复制，粘贴到对应聊天窗口即可。';}catch{receipt.textContent='无法自动复制，请手动复制：'+text;}}
  actions.append(ui.button('复制微信命令',{onClick:()=>chosen&&void copy('/meme '+chosen.id)}),
    ui.button('复制屏幕聊天请求',{onClick:()=>chosen&&void copy('发一张'+chosen.title+'表情')}));
  selected.append(title,picture,description,actions,receipt);
  const nav=ui.actions(),position=make('span');
  const prev=ui.button('上一页',{onClick:()=>void load(page-1)}),next=ui.button('下一页',{onClick:()=>void load(page+1)});
  nav.style.marginTop='16px';nav.append(prev,position,next);
  function buttons(){search.disabled=busy;prev.disabled=busy||page<=1;next.disabled=busy||page>=pages;}
  async function preview(item){
    const version=++previewVersion;chosen=item;selected.hidden=false;title.textContent=item.title+' · '+item.id;picture.hidden=true;picture.removeAttribute('src');
    description.textContent=item.context;receipt.textContent='正在读取动图…';
    try{
      const result=await ctx.invoke('preview',[{id:item.id}]);
      if(signal.aborted||version!==previewVersion)return;
      picture.alt=item.title;picture.onload=()=>{if(version===previewVersion)receipt.textContent='预览就绪。选择下方命令，在聊天中使用这张表情。';};
      picture.onerror=()=>{if(version===previewVersion)receipt.textContent='图片无法显示，请重新选择或检查素材。';};
      picture.src=result.src;picture.hidden=false;
    }catch{if(!signal.aborted&&version===previewVersion)receipt.textContent='这张表情暂不可用，请检查本机素材是否已安装。';}
  }
  async function load(wanted){
    if(busy||signal.aborted)return;busy=true;buttons();status.textContent='正在读取表情…';
    try{
      const result=await ctx.invoke('list',[{query:query.value.trim(),page:Math.max(1,wanted)}]);
      if(signal.aborted)return;page=result.page;pages=result.pages;grid.replaceChildren();
      status.textContent=result.total?`找到 ${result.total} 张 · 已登记 ${result.installed} 张原版动图`:'没有找到表情，请换个关键词或检查素材安装。';
      for(const item of result.items){
        const card=make('article');card.style.cssText='padding:14px;border:1px solid #a1a1aa;border-radius:10px;display:flex;flex-direction:column;gap:8px';
        const label=make('strong',item.title),meta=make('small',item.id+' · '+item.emotion),hint=make('p',item.context);hint.style.cssText='font-size:13px;line-height:1.6;margin:0;flex:1';
        card.append(label,meta,hint,ui.button('预览 '+item.title,{onClick:()=>void preview(item)}));grid.append(card);
      }
      position.textContent=`第 ${page} / ${pages} 页`;
    }catch{if(!signal.aborted)status.textContent='读取失败，请稍后重试。';}
    finally{busy=false;if(!signal.aborted)buttons();}
  }
  form.addEventListener('submit',event=>{event.preventDefault();void load(1);},{signal});
  signal.addEventListener('abort',()=>{previewVersion++;picture.removeAttribute('src');},{once:true});
  const credit=make('p');credit.style.cssText='font-size:12px;opacity:.75;margin-top:20px';
  credit.append('表情作者：');const author=make('a','可以叫我云大人 / 雾雪');author.href='https://kiriyuki.com';author.target='_blank';author.rel='noopener noreferrer';credit.append(author,' · 保留署名，仅供非商业使用。');
  root.append(ui.section('大肥鱼表情','按名称或情绪找图，预览后复制聊天命令。屏幕聊天页支持动图；微信播放效果以手机端为准。'),form,status,selected,grid,nav,credit);
  buttons();void load(1);
}


export default {panels:{stickers:{mount:mountStickers},preferences:{mount(ctx){
  const {root,ui,signal}=ctx;
  const status=ui.h('div'),message=ui.msgline(),actions=ui.actions();
  const form=document.createElement('form');form.style.cssText='display:grid;gap:16px;max-width:520px;margin:20px 0';
  const enabled=document.createElement('input');enabled.type='checkbox';
  const minutes=document.createElement('input');minutes.type='number';minutes.min='1';minutes.max='1440';minutes.step='1';
  const hourly=document.createElement('input');hourly.type='number';hourly.min='0';hourly.max='12';hourly.step='1';
  minutes.required=true;hourly.required=true;
  function field(label,input,hint){
    const row=document.createElement('label');row.style.cssText='display:grid;gap:6px';
    const text=document.createElement('span');text.textContent=label;
    const help=document.createElement('small');help.textContent=hint;help.style.opacity='.7';
    if(input.type==='checkbox'){const line=document.createElement('span');line.append(input,' ',text);row.append(line,help);}
    else {input.style.cssText='padding:9px;border:1px solid #a1a1aa;border-radius:8px;background:transparent;color:inherit;width:100%;box-sizing:border-box';row.append(text,input,help);}
    return row;
  }
  form.append(field('允许主动发消息',enabled,'关闭后仍正常回复你发来的消息。'),
    field('最小间隔（分钟）',minutes,'1～1440 分钟；这是发送间隔下限，不是定时必发。'),
    field('每小时最多（次）',hourly,'0～12 次；一次回复拆成两条短消息，仍算一次主动交流。'));
  let loading=false,actual=null,dirty=false;
  const save=ui.button('保存到云端',{onClick:()=>void update(true)});
  const refresh=ui.button('读取云端设置',{onClick:()=>void update(false)});
  function buttons(){save.disabled=loading||!actual||!dirty;refresh.disabled=loading;for(const input of [enabled,minutes,hourly])input.disabled=loading||!actual;}
  for(const input of [enabled,minutes,hourly])input.addEventListener('input',()=>{dirty=true;message.textContent='有未保存的修改';message.classList.remove('bad');buttons();},{signal});
  form.addEventListener('submit',e=>{e.preventDefault();void update(true);},{signal});
  async function update(saving){
    if(loading||signal.aborted)return;
    if(saving&&(!actual||!form.reportValidity()))return;
    loading=true;buttons();message.textContent=saving?'正在保存到云端…':'正在读取云端…';message.classList.remove('bad');
    try{
      const state=await ctx.invoke(saving?'save':'state',saving?[{values:{proactive:enabled.checked,proactive_cooldown:Number(minutes.value)*60,proactive_per_hour:Number(hourly.value)},expected:actual}]:[]);
      if(signal.aborted)return;
      if(!state?.values)throw new Error('云端尚未提供设置接口');
      actual=state.values;enabled.checked=actual.proactive;minutes.value=String(actual.proactive_cooldown/60);hourly.value=String(actual.proactive_per_hour);dirty=false;
      status.replaceChildren(ui.kv([{k:'云端已生效',v:!actual.proactive||actual.proactive_per_hour===0?'主动消息已暂停':`至少间隔 ${actual.proactive_cooldown/60} 分钟，每小时最多 ${actual.proactive_per_hour} 次`},
        {k:'微信连接',v:state.wechat?.state==='connected'?(state.wechat.ready?'已连接，可发送':'已连接，等待你发一条消息恢复发送会话'):'暂未连接'},
        {k:'勿扰',v:state.dnd?'已开启，暂不主动发消息':'未开启'}]));
      message.textContent=saving?'已保存，云端立即生效':'已读取云端实际设置';
    }catch{if(!signal.aborted){message.textContent=saving?'未确认保存成功。请检查连接并重新读取；不会自动重试或补存。':'无法读取云端。请检查网络与同步连接后重试。';message.classList.add('bad');}}
    finally{loading=false;if(!signal.aborted)buttons();}
  }
  actions.append(save,refresh);
  root.append(ui.section('主动消息设置','控制云端微信的主动聊天、分享与提醒频率。普通回复不受影响，人物性格保持不变。'),status,form,actions,message);
  buttons();void update(false);
}}}};
