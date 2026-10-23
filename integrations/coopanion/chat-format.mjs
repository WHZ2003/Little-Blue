// Applied after the pet script parser: formatting does not consume animation tags.
const tic = /(^|[，,。！？!?；;\s])库[.。…~～!！]*(?=$|\s)/gmu;
const clean = text => text.replace(tic, (_, prefix) => /\s/u.test(prefix) ? ' ' : prefix)
  .replace(/\s+/gu, ' ').replace(/^[ ,，]+|[ ,，]+$/gu, '');

export function shortBeats(beats) {
  return beats.flatMap(beat => {
    const text = clean(beat.text), chars = Array.from(text), parts = [];
    let offset = 0;
    while (chars.length) {
      let end = Math.min(30, chars.length);
      if (chars.length > 30) {
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
  return Array.from(text).length <= 30 && !/[\r\n]/u.test(text) && clean(text) === text;
}
