import {useEffect,useRef,useState} from 'react';

export type Mode='demo'|'live';
export type Num=number|null|undefined;
export type FlowState={state:string;trade_day:string|null;expected_day:string|null;point_count:number;complete:boolean;
  unit:'CNY';cumulative:boolean;delay_endpoint:boolean};
export type Packet<T>={data:T|null;source:string|null;source_url:string|null;fetched_at:string|null;market_time:string|null;error:string|null;is_stale:boolean;cached:boolean;mode:Mode;note?:string|null;flow?:FlowState};
export type Quote={code:string;name:string;price:number;pct:Num;amount:Num;previous_close:Num;open:Num;volume:Num;turnover:Num;amplitude:Num;vol_ratio:Num;float_cap:Num;market_cap:Num;market_time:string|null;is_stale:boolean;quote_fetched_at?:string;book?:{bid:Num;ask:Num;bid_lots:Num;ask_lots:Num}[]};
export type Bar={t:string;open:number;close:number;high:number;low:number;volume:number;ma5:Num;ma20:Num;macd:Num;dif:Num;dea:Num;rsi:Num};
export type Bars={items:Bar[];adjust:string;volume_unit:string};
export type Part={label:string;score:Num;weight:number};
export type Score={score:Num;coverage:number;parts:Part[]};
export type Sector={code:string;name:string;rank:number;rank_delta:Num;pct:Num;main_net:number;main_pct:Num;amount:Num;leader:string|null;heat:Score;net_1:Num;net_5:Num;net_10:Num;up:Num;down:Num};
export type Ranking={items:Sector[];total:number};
export type Clock={time:string;label:string;latest_day:string|null;phase:string;active:boolean;tail:boolean;calendar_source:string|null;calendar_error:string|null;skill_version?:string;quote_interval_s?:number};
export type Mood={up:Num;down:Num;flat:Num;covered:Num;total:Num;amount:Num;limit_up:Num;limit_down:Num;broken:Num;break_rate:Num;streak:Num;premium:Num;strong:Num;heat:Score;sources:Record<string,Partial<Packet<unknown>>>};
export type Alert={id:string;code:string;name:string;event:string;value:number;threshold:number;unit:string;market_time:string;source:string;source_url:string|null;previous:Num;mode:Mode};
export type Flow={t:string;main_net:Num;large_net?:Num;super_net?:Num};
export type Role=Score&{code:string;name:string;pct:Num;relative:Num;amount:Num;main_net:Num;return_5:Num};
export type RoleData={roles:Record<string,Role[]>;sample:number;total:number;price_coverage:number;flow_coverage:number;members:(Quote&{main_net:Num})[];board_pct:Num};
export type Tail={window:boolean;phase:string;day:string|null;items:{code:string;name:string;move:number;amount:Num;pct:Num;from:string;to:string;heat:Score}[];sectors:{name:string;kind:string;change:Num;rank_delta:Num;leader:string|null;leader_code:string|null}[]};
export type News={id:string;title:string;url:string|null;time:string};

export function saved<T>(key:string,fallback:T):T{
  try {const value=localStorage.getItem('pulse2:'+key);return value===null?fallback:JSON.parse(value)} catch {return fallback}
}
export function useSaved<T>(key:string,fallback:T){
  const [value,set]=useState(()=>saved(key,fallback));
  useEffect(()=>{try{localStorage.setItem('pulse2:'+key,JSON.stringify(value))}catch{/* storage unavailable */}},[key,value]);
  return [value,set] as const;
}
export const failure=<T,>(mode:Mode,error:string):Packet<T>=>({data:null,source:null,source_url:null,fetched_at:null,market_time:null,error,is_stale:true,cached:false,mode});
export function useFeed<T>(path:string|null,mode:Mode,interval:number,revision:number,body?:string){
  const url=path?path+(path.includes('?')?'&':'?')+'mode='+mode:'';
  const key=url?(body===undefined?url:url+' '+body):'';
  const [state,set]=useState<{key:string;packet:Packet<T>}|null>(null);
  useEffect(()=>{
    if(!key)return;
    const controller=new AbortController();
    let timer:number|undefined;
    async function load(){
      try{
        const request=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body};
        const res=await fetch(url,{...request,signal:controller.signal,cache:'no-store'});
        if(!res.ok)throw new Error('服务接口 HTTP '+res.status);
        const packet=await res.json() as Packet<T>;
        if(!controller.signal.aborted)set({key,packet});
      }catch(e){
        if(!controller.signal.aborted)set(prev=>({key,packet:{...(prev?.key===key?prev.packet:failure<T>(mode,'')),error:e instanceof Error?e.message:'请求失败',is_stale:true}}));
      }finally{
        if(interval&&!controller.signal.aborted)timer=window.setTimeout(load,interval);
      }
    }
    void load();
    return ()=>{controller.abort();window.clearTimeout(timer)};
  },[url,key,mode,interval,revision,body]);
  return state?.key===key?state.packet:null;
}
type StreamState={mode:Mode;online:boolean;clock?:Clock;indices?:Packet<Quote[]>;quotes?:Packet<Quote[]>};
export function useStream(mode:Mode,codes:string,revision:number){
  const desired=useRef(codes), socket=useRef<WebSocket|null>(null), lastRevision=useRef(revision);
  desired.current=codes;
  const [state,set]=useState<StreamState>({mode,online:false});
  useEffect(()=>{
    let ws:WebSocket,retry:number,watchdog:number,ended=false,delay=1200;
    let lastMessage=0,interval=mode==='demo'?5:8;
    function deadline(seconds:number){
      clearTimeout(watchdog);
      watchdog=window.setTimeout(()=>ws.close(),seconds*1000);
    }
    function connect(){
      const subscribed=desired.current;
      ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/ws/market?mode='+mode+'&codes='+encodeURIComponent(subscribed));
      socket.current=ws;
      deadline(20);
      ws.onopen=()=>{if(!ended&&desired.current!==subscribed)ws.send(JSON.stringify({type:'subscribe',codes:desired.current}))};
      ws.onmessage=e=>{
        if(ended)return;
        let p;
        try{p=JSON.parse(e.data)}catch{return}
        if(p.mode!==mode||!p.clock)return;
        delay=1200;lastMessage=Date.now();interval=p.clock.quote_interval_s??interval;
        deadline(interval*2+15);
        set({mode,online:true,clock:p.clock,indices:p.indices,quotes:p.quotes});
      };
      ws.onerror=()=>ws.close();
      ws.onclose=()=>{
        clearTimeout(watchdog);
        if(socket.current===ws)socket.current=null;
        if(!ended){
          set(s=>s.mode===mode?{...s,online:false}:{mode,online:false});
          retry=window.setTimeout(connect,delay);
          delay=Math.min(delay*2,12000);
        }
      };
    }
    function resume(){
      if(document.visibilityState==='visible'&&ws.readyState===WebSocket.OPEN&&Date.now()-lastMessage>interval*1000)
        ws.send(JSON.stringify({type:'refresh'}));
    }
    set(s=>s.mode===mode?{...s,online:false}:{mode,online:false});
    connect();
    document.addEventListener('visibilitychange',resume);
    return ()=>{
      ended=true;clearTimeout(retry);clearTimeout(watchdog);
      document.removeEventListener('visibilitychange',resume);
      if(socket.current===ws)socket.current=null;
      ws.close();
    };
  },[mode]);
  useEffect(()=>{
    if(socket.current?.readyState===WebSocket.OPEN)socket.current.send(JSON.stringify({type:'subscribe',codes}));
  },[codes]);
  useEffect(()=>{
    if(lastRevision.current===revision)return;
    lastRevision.current=revision;
    if(socket.current?.readyState===WebSocket.OPEN)socket.current.send(JSON.stringify({type:'refresh'}));
  },[revision]);
  return state.mode===mode?state:{mode,online:false};
}
export function latestPacket<T>(stream?:Packet<T>|null,http?:Packet<T>|null){
  if(!stream)return http??null;
  if(!http)return stream;
  if(http.error&&http.data===null&&stream.data!==null)return {...stream,error:http.error,is_stale:true};
  const time=(value:string|null)=>value?Date.parse(value):0;
  return time(http.fetched_at)>=time(stream.fetched_at)?http:stream;
}
export function ticker(raw:string){
  const m=raw.trim().toLowerCase().match(/^(?:(sh|sz|bj))?([0-9]{6})(?:\.(sh|sz|bj))?$/);
  if(!m||(m[1]&&m[3]))return null;
  const [,prefix,n,suffix]=m;
  return (prefix||suffix||(/^(92|4|8)/.test(n)?'bj':/^[569]/.test(n)?'sh':'sz'))+n;
}
export const fmt=(value:Num,dec=2)=>value==null?'—':value.toLocaleString('zh-CN',{minimumFractionDigits:dec,maximumFractionDigits:dec});
export const signed=(value:Num,dec=2)=>value==null?'—':(value>0?'+':'')+fmt(value,dec);
export const percent=(value:Num)=>value==null?'—':signed(value)+'%';
export const money=(value:Num,sign=false)=>value==null?'—':(sign?signed:fmt)(value/1e8)+' 亿';
export const tone=(value:Num)=>value==null||value===0?'muted':value>0?'rise':'fall';
export const timeText=(value?:string|null)=>value?.length===10?value:value?value.slice(0,10)+' '+value.slice(11,19):'未提供';
export function externalUrl(value:string|null|undefined){
  if(!value||!/^https:\/\//i.test(value)||/[\u0000-\u0020\u007f]/u.test(value))return undefined;
  try{
    const url=new URL(value);
    if(url.protocol==='https:'&&!url.username&&!url.password)return url.href;
  }catch{/* ignore malformed upstream links */}
  return undefined;
}
export function csvValue(value:string|number|null|undefined){
  const raw=String(value??'');
  const dangerous=typeof value==='string'&&/^[\s\p{Cf}]*[=+\-@＝＋－＠\t\r\n]/u.test(raw);
  return '"'+(dangerous?"'"+raw:raw).replaceAll('"','""')+'"';
}
export function csv(filename:string, rows:(string|number|null|undefined)[][]){
  const value=rows.map(row=>row.map(csvValue).join(',')).join('\r\n');
  const url=URL.createObjectURL(new Blob(['\ufeff',value],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=filename;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),500);
}
