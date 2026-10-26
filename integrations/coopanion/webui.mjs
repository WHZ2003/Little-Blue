export default {panels:{preferences:{mount(ctx){
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
