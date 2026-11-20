import {ticker,type Mode} from './data';
import {newTrack,TRACK_LIMIT,type Tracked} from './tracking';

export const WATCH_LIMIT=60;
// Keep all saved symbols subscribed, plus one additional symbol for the open chart.
export const POOLED_QUOTE_LIMIT=WATCH_LIMIT+TRACK_LIMIT;
type Candidate={code:string;name:string;board:string;role:string;boardNet?:number};
const roles=new Set(['龙头','中军','低位','补涨']);

export function readObservationPool(raw:string):{asOf:string;selections:Candidate[]} {
  const payload:unknown=JSON.parse(raw.replace(/^\uFEFF/,''));
  if(typeof payload!=='object'||!payload||!('schema' in payload)||payload.schema!=='pulse-observation-pool-v1'||
     !('asOf' in payload)||typeof payload.asOf!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(payload.asOf)||
     !('selections' in payload)||!Array.isArray(payload.selections)||!payload.selections.length||payload.selections.length>120)
    throw new Error('无法识别观察池文件，请选择 PULSE 双池 JSON');
  const selections=payload.selections.map((row:unknown)=>{
    if(typeof row!=='object'||!row||!('code' in row)||typeof row.code!=='string'||
       !('name' in row)||typeof row.name!=='string'||!row.name.trim()||row.name.length>30||
       !('board' in row)||typeof row.board!=='string'||!row.board.trim()||row.board.length>30||
       !('role' in row)||typeof row.role!=='string'||!roles.has(row.role))
      throw new Error('文件包含无法识别的证券或板块角色');
    const code=ticker(row.code);
    if(!code||!/^sh(?:60|68)\d{4}$|^sz(?:00|30)\d{4}$|^bj(?:[48]\d|92)\d{4}$/.test(code))
      throw new Error('双池只接受 A 股股票代码');
    const boardNet='boardNet' in row&&typeof row.boardNet==='number'&&Number.isFinite(row.boardNet)?row.boardNet:undefined;
    return {code,name:row.name.trim(),board:row.board.trim(),role:row.role,boardNet};
  });
  return {asOf:payload.asOf,selections:[...new Map(selections.map(entry=>[entry.code,entry])).values()]};
}

export function appendWatchCodes(existing:string[],targets:string[],tracking:Record<Mode,Tracked[]>){
  const watch=new Set(existing);
  const subscriptions=Object.values(tracking).map(tracked=>new Set([...existing,...tracked.map(entry=>entry.code)]));
  let skipped=0;
  for(const code of new Set(targets)){
    if(watch.has(code))continue;
    if(watch.size>=WATCH_LIMIT||subscriptions.some(pool=>!pool.has(code)&&pool.size>=POOLED_QUOTE_LIMIT)){
      skipped++;
      continue;
    }
    watch.add(code);
    subscriptions.forEach(pool=>pool.add(code));
  }
  return {watch:[...watch],skipped};
}

export function mergeObservationPools(watch:string[],tracking:Record<Mode,Tracked[]>,pool:ReturnType<typeof readObservationPool>){
  const tracked=tracking.live;
  const nextWatch=new Set(watch),nextTracked=new Map(tracked.map(entry=>[entry.code,entry]));
  const combined=new Set([...nextWatch,...nextTracked.keys()]);
  const demoCombined=new Set([...nextWatch,...tracking.demo.map(entry=>entry.code)]);
  let skipped=0;
  for(const entry of pool.selections){
    const inWatch=nextWatch.has(entry.code),inTracked=nextTracked.has(entry.code);
    if((!inWatch&&nextWatch.size>=WATCH_LIMIT)||(!inTracked&&nextTracked.size>=TRACK_LIMIT)||
       (!combined.has(entry.code)&&combined.size>=POOLED_QUOTE_LIMIT)||
       (!demoCombined.has(entry.code)&&demoCombined.size>=POOLED_QUOTE_LIMIT)){skipped++;continue}
    nextWatch.add(entry.code);
    if(!inTracked){
      const flow=entry.boardNet===undefined?'':` · 板块30日净额 ${entry.boardNet>0?'+':''}${(entry.boardNet/1e8).toFixed(2)}亿元`;
      nextTracked.set(entry.code,{...newTrack(entry.code,entry.name),note:`${entry.board} · ${entry.role}观察${flow} · ${pool.asOf}`});
    }
    combined.add(entry.code);
    demoCombined.add(entry.code);
  }
  return {watch:[...nextWatch],tracked:[...nextTracked.values()],watchAdded:nextWatch.size-watch.length,
    trackAdded:nextTracked.size-tracked.length,skipped};
}
