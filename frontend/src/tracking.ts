import type {Quote} from './data';

export const TRACK_LIMIT=60;
export type Sample={time:string;price:number;pct:number|null};
export type TargetEvent={time:string;price:number;target:number;direction:'upper'|'lower'};
export type Tracked={
  code:string;name:string;note:string;upper:number|null;lower:number|null;
  baseline:Sample|null; samples:Sample[];events:TargetEvent[];
};

export function newTrack(code:string,name=code.toUpperCase()):Tracked{
  return {code,name,note:'',upper:null,lower:null,baseline:null,samples:[],events:[]};
}

export function observe(stock:Tracked,quote:Quote,canTrigger:boolean):Tracked{
  if(quote.is_stale||!quote.market_time||!Number.isFinite(quote.price)||quote.price<=0)return stock;
  const stamp=Date.parse(quote.market_time);
  const prior=stock.samples.at(-1);
  if(!Number.isFinite(stamp)||(prior&&stamp<=Date.parse(prior.time)))return stock;
  const sample={time:quote.market_time,price:quote.price,pct:quote.pct??null};
  const events=stock.events.slice();
  if(canTrigger&&prior){
    if(stock.upper!==null&&prior.price<stock.upper&&sample.price>=stock.upper)
      events.push({time:sample.time,price:sample.price,target:stock.upper,direction:'upper'});
    if(stock.lower!==null&&prior.price>stock.lower&&sample.price<=stock.lower)
      events.push({time:sample.time,price:sample.price,target:stock.lower,direction:'lower'});
  }
  return {...stock,name:quote.name||stock.name,baseline:stock.baseline??sample,
    samples:[...stock.samples,sample].slice(-60),events:events.slice(-30)};
}
