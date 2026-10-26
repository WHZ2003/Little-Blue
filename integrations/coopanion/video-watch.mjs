import { randomUUID } from 'node:crypto';
export const VIDEO_KEY=Symbol.for('dafeiyu.video-watch');
const canonical=url=>/^https:\/\/www\.bilibili\.com\/video\/BV[0-9A-Za-z]{10}\/\?p=1$/.test(url);

/** Durable start fencing and receipts; no chat-history events can launch a video. */
export class VideoRelay {
  constructor(shared){
    this.shared=shared;this.session=randomUUID().replaceAll('-','');this.started=new Set();this.lastSync=0;
    shared.state.videoReports??=[];
    // Old runtimes never resume/replay a physical operation.
    if(shared.state.videoWork){
      const w=shared.state.videoWork;
      if(!shared.state.videoReports.some(r=>r.id===w.id&&r.state!=='playing'))
        shared.state.videoReports.push({id:w.id,token:w.token,session:w.session,state:'failed',reason:'restarted'});
      delete shared.state.videoWork;
    }
  }
  payload(){
    const bridge=globalThis[VIDEO_KEY],work=this.shared.state.videoWork;
    return {session:this.session,ready:!!bridge?.ready(),reports:this.shared.state.videoReports.slice(0,8),
      ...(work?.phase==='claiming'?{claim:{id:work.id,token:work.token}}:{})};
  }
  accept(response){
    if(!response)return;
    this.lastSync=Date.now();
    this.shared.state.videoReports=this.shared.state.videoReports.filter(r=>!response.accepted.includes(r.id+':'+r.state));
    let work=this.shared.state.videoWork;
    if(work&&response.active&&!response.active.includes(work.id)){
      if(work.phase==='started')void globalThis[VIDEO_KEY]?.cancel('interrupted');
      delete this.shared.state.videoWork;work=null;
    }
    if(work&&response.claimed===work.id&&work.phase==='claiming'&&!this.started.has(work.id)){
      // Checkpoint before any browser operation. Restart after this point reports interruption.
      work.phase='started';this.shared.save();this.started.add(work.id);
      const bridge=globalThis[VIDEO_KEY];
      const report=data=>{
        const receipt={...data,id:work.id,token:work.token,session:work.session};
        if(!this.shared.state.videoReports.some(r=>r.id===work.id&&r.state===receipt.state))this.shared.state.videoReports.push(receipt);
        if(receipt.state!=='playing'&&this.shared.state.videoWork?.id===work.id)delete this.shared.state.videoWork;
        this.shared.save();
      };
      if(!bridge||!bridge.ready())report({state:'failed',reason:'unavailable'});
      else Promise.resolve().then(()=>bridge.start({id:work.id,url:work.url,permitted:()=>Date.now()-this.lastSync<75000},report)).catch(()=>report({state:'failed',reason:'open_failed'}));
    }
    if(!work&&response.job){
      const job=response.job;
      if(job.session!==this.session||!canonical(job.url)||job.lease<=response.server_time)return;
      this.shared.state.videoWork={...job,phase:'claiming'};
    }
    this.shared.save();
  }
  async stop(){ await globalThis[VIDEO_KEY]?.cancel('interrupted'); }
}
