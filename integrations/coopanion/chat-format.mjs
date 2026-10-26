// Applied after the pet script parser: formatting does not consume animation tags.
const tic = /(^|[，,。！？!?；;\s])库[.。…~～!！]*(?=$|\s)/gmu;
const clean = text => text.replace(tic, (_, prefix) => /\s/u.test(prefix) ? ' ' : prefix)
  .replace(/\s+/gu, ' ').replace(/^[ ,，]+|[ ,，]+$/gu, '');

export function shortBeats(beats, live=true) {
  const bridge=globalThis[Symbol.for('dafeiyu.shared-preferences')];
  if(live&&bridge?.receipt){
    if(bridge.spoke)return [];
    bridge.spoke=true;return [{text:bridge.receipt,actions:[],anchors:[]}];
  }
  if(live&&/记住了|偏好.{0,10}(已保存|生效)|已保存.{0,10}偏好|设置好了/.test(beats.map(b=>b.text).join('')))
    return [{text:'这项偏好还没有取得保存回执，不能说已经生效。',actions:[],anchors:[]}];
  const prefs=bridge?.values||{}, limit=prefs.short_limit||30;
  const total=beats.map(b=>b.text).join('\n');
  const long=total.startsWith('日记 · ')||beats.some(b=>Array.from(b.text).length>limit)||Array.from(total).length>2*limit;
  if(prefs.message_mode==='whole'||long&&(prefs.long_form_mode||'whole')==='whole'){
    let offset=0;const anchors=[];
    for(const b of beats){
      if(offset&&b.actions?.length)anchors.push({at:offset,actions:b.actions});
      anchors.push(...(b.anchors||[]).map(a=>({...a,at:a.at+offset})));offset+=b.text.length+1;
    }
    return total.trim()?[{text:total,actions:beats[0]?.actions||[],anchors}]:beats;
  }
  return beats.flatMap(beat => {
    const text = clean(beat.text), chars = Array.from(text), parts = [];
    let offset = 0;
    while (chars.length) {
      let end = Math.min(limit, chars.length);
      if(prefs.message_mode==='split_short'){
        const boundary=chars.slice(0,end).findIndex(c=>'。！？!?'.includes(c));
        if(boundary>=0)end=boundary+1;
      }
      if (chars.length > limit) {
        for (let p = end - 1; p >= Math.floor(end / 2); p--) {
          if ('。！？!?；;，,、 '.includes(chars[p])) { end = p + 1; break; }
        }
      }
      const raw = chars.splice(0, end).join('');
      const lead = raw.length - raw.trimStart().length;
      parts.push({text:raw.trim(), start:offset+lead, end:offset+raw.length});
      offset += raw.length;
    }
    if (!parts.length) return beat.actions.length || beat.anchors.length ? [{...beat,text:'',anchors:beat.anchors.map(a=>({...a,at:0}))}] : [];
    const anchors = beat.anchors.map(a=>({...a,at:Math.min(text.length,clean(beat.text.slice(0,a.at)).length)}));
    return parts.map((p,i)=>({text:p.text, actions:i===0?beat.actions:[],
      anchors:anchors.filter(a=>(i===0||a.at>=p.start)&&(i===parts.length-1||a.at<parts[i+1].start))
        .map(a=>({...a,at:Math.max(0,Math.min(p.text.length,a.at-p.start))}))}));
  });
}

export function validQuestion(text) {
  const prefs=globalThis[Symbol.for('dafeiyu.shared-preferences')]?.values||{};
  return Array.from(text).length <= (prefs.message_mode==='whole'?2000:(prefs.short_limit||30)) && !/[\r\n]/u.test(text) && clean(text) === text;
}
