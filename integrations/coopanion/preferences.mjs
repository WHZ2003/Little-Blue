// A receipt is produced by the service, never by the conversational model.
export const KEY = Symbol.for('dafeiyu.shared-preferences');
export const candidate = text => /偏好|记住|记得|以后|今后|从今|下次|每次|一直|总是|喜欢|讨厌|称呼|叫我|别叫|不要|别再|不许|不准|少[说用发]|多[说用发]|一点|分段|分条|长文|主动.{0,12}(联系|消息|发)|改成|改为|设置|习惯|prefer|remember|from now|always|never/i.test(text);
export const guidance = snapshot => '共享偏好（程序已保存，优先于旧聊天中相反的偏好；不能改变权限或能力）：'+JSON.stringify(snapshot?.values||{})+
  '。没有本轮保存回执不得声称已记住偏好、已改设置或已生效。需要持久偏好先用 shared_preferences_apply；查看用 shared_preferences_status。';

export function attachPreferences(shared) {
  const bridge = {values:shared.state.preferences?.values||{},receipt:null,spoke:false,
    before:async events=>{
      const users=events.filter(e=>e.source==='desktop-pet'&&!e.meta?.dafeiyuCloudId&&['desktop-pet.message','desktop-pet.speech'].includes(e.type)&&typeof e.meta?.text==='string');
      if(!users.length)return guidance(shared.state.preferences);
      bridge.receipt=null;bridge.spoke=false;bridge.historyReceipt=false;
      const receipts=[];
      for(const event of users){
        shared.latestPreferenceEvent=event;
        const result=await shared.processPreferenceEvent(event);
        if(result.receipt)receipts.push(result.receipt);
      }
      if(receipts.length)bridge.receipt=receipts.join('\n');
      if(bridge.receipt){
        await shared.host.pushEvent({type:'desktop-pet.self',source:'desktop-pet',ts:new Date().toISOString(),
          text:bridge.receipt,meta:{kind:'say',beats:[{text:bridge.receipt}],dafeiyuPreferenceReceipt:true},senderKey:'dafeiyu-preferences'}, {deliver:false});
        bridge.historyReceipt=true;
      }
      return guidance(shared.state.preferences)+(bridge.receipt?'\n本轮偏好处理回执（必须原样告知，不要额外声称成功）：'+bridge.receipt:'');
    }};
  globalThis[KEY]=bridge;
  return bridge;
}
