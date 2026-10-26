import { autoCloseVideoEnabled } from './owned-page.ts';
export const VIDEO_KEY=Symbol.for('dafeiyu.video-watch');
const pause=(ms:number)=>new Promise<void>(r=>setTimeout(r,ms));
class WatchFailure extends Error {}

/** Narrow paired request executor. It never reads subtitles as a substitute for playback. */
export class RemoteWatch {
  active=false;
  private reason='';
  private done:Promise<void>|null=null;
  constructor(private world:any,private sleep=pause,private now=()=>Date.now()) {}
  ready(){return !this.active&&!!this.world.host&&this.world.mode==='idle';}
  start(job:{id:string,url:string,permitted?:()=>boolean},report:(data:any)=>void){
    if(!this.ready()){report({state:'failed',reason:'busy'});return Promise.resolve();}
    if(!/^https:\/\/www\.bilibili\.com\/video\/BV[0-9A-Za-z]{10}\/\?p=1$/.test(job.url)){
      report({state:'failed',reason:'unsupported'});return Promise.resolve();
    }
    this.active=true;this.reason='';
    this.done=this.run(job,report).finally(()=>{this.active=false;this.done=null;});
    return this.done;
  }
  async cancel(reason='interrupted'){
    if(!this.active)return;
    this.reason=reason;
    await this.done;
  }
  private async run(job:{url:string,permitted?:()=>boolean},report:(data:any)=>void){
    const w=this.world,b=w.browser;
    let ticket:any=null,owner:any=null,title='',duration=0,position=0,played=0,frames=0,started=false,finished=false;
    let cleanup='none',failure='open_failed';
    const sameVideo=(url:string)=>{
      try{
        const actual=new URL(url),expected=new URL(job.url);
        return actual.origin===expected.origin&&actual.pathname.replace(/\/$/,'')===expected.pathname.replace(/\/$/,'')
          &&(actual.searchParams.get('p')||'1')==='1';
      }catch{return false;}
    };
    const check=()=>{
      if(this.reason)throw new WatchFailure(this.reason);
      if(job.permitted&&!job.permitted())throw new WatchFailure('disconnected');
      if(ticket){
        const current=b.ownershipTicket();
        // Bilibili rewrites tracking parameters after playback begins. Such a navigation
        // may change the ticket generation, but never grants/reclaims page ownership.
        if(current?.owner===owner&&owner.eligible&&sameVideo(current.url))ticket=current;
        if(!b.ticketMatches(ticket)||!ticket.owner.eligible)throw new WatchFailure('taken_over');
      }
    };
    // Native page operations already have deadlines. This prevents an unresponsive renderer from holding a job forever.
    const bounded=async<T>(promise:Promise<T>,ms=20000):Promise<T>=>{
      let timer:any;
      try{return await Promise.race([promise,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(new WatchFailure('stalled')),ms);})]);}
      finally{clearTimeout(timer);}
    };
    try{
      check();w.mode='browsing';
      await b.freshOwnedPage();owner=b.ownershipTicket()?.owner;
      await b.goto(job.url);
      ticket=b.ownershipTicket();check();
      if(!await bounded(b.waitForVideo(15000)))throw new WatchFailure('open_failed');
      check();
      const info:any=await bounded(b.videoInfo());
      const expected=job.url.match(/BV[0-9A-Za-z]{10}/)![0];
      if(!info||info.bvid!==expected||info.p!==1)throw new WatchFailure('open_failed');
      title=String(info.title||'').replace(/[\r\n\x00-\x1f]/g,' ').slice(0,200);
      duration=Number(info.duration);
      if(!(duration>0&&duration<=7200))throw new WatchFailure('unsupported');
      check();w.video=info;w.mode='watching';
      await bounded(b.play(0));check();
      let last=0,lastAt=this.now(),lastProgress=lastAt,begin=lastAt,sampled=0,resets=0;
      while(true){
        check();
        const pb:any=await bounded(b.playback());check();
        const at=this.now();
        if(!pb){throw new WatchFailure('closed');}
        position=Number(pb.t)||0;w.lastPlayback=pb;
        if(pb.duration>0&&pb.duration<=7200)duration=pb.duration;
        const delta=position-last,elapsed=(at-lastAt)/1000;
        if(delta>0.01){
          // A seek or fast-forward cannot manufacture full-watch evidence.
          if(delta>elapsed*1.25+1.5){
            // Bilibili can restore account history after our initial play(0).
            // History restoration can arrive after the first advancing frame.
            // Only correct it during startup, with ownership intact; discard prior coverage.
            if(at-begin<5000&&played<5&&resets<2){
              check();await bounded(b.seek(0));check();await bounded(b.play(0));check();
              resets++;played=0;last=0;lastAt=this.now();lastProgress=lastAt;await this.sleep(500);continue;
            }
            throw new WatchFailure('incomplete');
          }
          played+=Math.min(delta,elapsed*1.1+0.1);lastProgress=at;
          if(!started){started=true;report({state:'playing',title,position,duration,played,frames});}
        }
        if(position<last-1)throw new WatchFailure('incomplete');
        if(pb.ended){
          if(!started||position<duration-1.5||played<Math.max(.5,duration*.9))throw new WatchFailure('incomplete');
          finished=true;break;
        }
        if(at-lastProgress>(started?45000:25000))throw new WatchFailure(started?'stalled':'play_failed');
        if(at-begin>Math.min(3*3600000,duration*2000+120000))throw new WatchFailure('stalled');
        if(at-sampled>5000){
          const sample:any=await bounded(b.sample(320,180,.6),10000).catch(()=>null);
          check();if(sample?.jpeg)frames++;sampled=at;
        }
        last=position;lastAt=at;
        await this.sleep(500);
      }
    }catch(error){failure=error instanceof WatchFailure?error.message:'open_failed';}
    finally{
      const current=b.ownershipTicket();
      if(current?.owner===owner)ticket=current;
      // No more playback/cleanup calls once the page was taken over or replaced.
      if(ticket&&b.ticketMatches(ticket)&&ticket.owner.eligible){
        if(autoCloseVideoEnabled()||!finished)cleanup=await bounded(b.finishOwnedVideo(ticket),15000).catch(()=> 'failed');
        else cleanup='retained';
      }else if(ticket)cleanup='retained';
      w.stopObserving();w.mode=cleanup==='closed'?'idle':'browsing';
      if(cleanup==='closed'){w.resetVideo();w.currentBvid=null;w.lastPlayback=null;}
      report({state:finished?'completed':'failed',...(finished?{}:{reason:failure}),title,
        position:Math.min(7205,Math.max(0,position)),duration:Math.min(7200,Math.max(0,duration)),played:Math.min(8000,played),frames,cleanup});
    }
  }
}
