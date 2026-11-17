import {externalUrl,fmt,percent,timeText,tone,useFeed,type Mode,type Num} from './data';

type Group='us'|'macro'|'index'|'commodity'|'cfd';
type Quote={symbol:string;name:string;group:Group;unit:string;url:string;status:'available'|'loading'|'missing'|'stale';
  price?:Num;pct?:Num;close_pct?:Num;close?:Num;close_date?:string|null;market_time?:string|null;
  fetched_at?:string|null;delay_minutes?:Num;error?:string|null};
type Snapshot={rows:Quote[];coverage:number;total:number;stance:string;us_close_date:string|null;notes:{label:string;text:string}[]};
const state={available:'已接入',loading:'采集中',missing:'暂不可用',stale:'缓存 / 滞后'};
const groups:[Group,string][]=[['us','美股指数'],['index','股指期货'],['commodity','商品期货'],['macro','利率与汇率'],['cfd','相关商品与A50 CFD']];

export function GlobalMarket({mode,revision,onLive,onIdeas}:{mode:Mode;revision:number;onLive:()=>void;onIdeas:()=>void}){
  const packet=useFeed<Snapshot>(mode==='live'?'/api/market/global':null,mode,12_000,revision);
  const data=packet?.data;
  return <section className="panel research global-page" id="global">
    <header className="research-head global-head">
      <div><div className="kicker">09 / GLOBAL MARKETS</div><h2>全球行情与 A 股盘前线索</h2>
        <p>把已完成的美股收盘与当前股指期货分开看，再核对 A 股板块和开盘承接。</p></div>
      <button className="btn" onClick={onIdeas}>查看开收盘候选</button>
    </header>
    {mode==='demo'?<div className="empty global-empty"><p>此页连接外盘实际行情。切换到真实模式后开始采集。</p><button className="btn" onClick={onLive}>读取真实外盘</button></div>:!data?
      <div className="empty">{packet?.error??'正在连接全球行情接口…'}</div>:<>
      <div className="global-context">
        <div className="global-stance"><span>外盘观察</span><strong>{data.stance}</strong><small>有效报价 {data.coverage} / {data.total} · 美东收盘 {data.us_close_date??'待核对'}</small></div>
        {data.notes.map(n=><div key={n.label} className="global-note"><strong>{n.label}</strong><p>{n.text}</p></div>)}
      </div>
      {groups.map(([group,label])=><div key={group} className="global-section">
        <h3>{label}</h3>
        <div className="global-grid">{data.rows.filter(r=>r.group===group).map(r=><article key={r.symbol} className="global-quote">
          <div className="global-quote-top"><a href={externalUrl(r.url)} target="_blank" rel="noreferrer">{r.name} ↗</a><span className={'market-state '+r.status} title={r.error??''}>{state[r.status]}</span></div>
          <div className="global-quote-price"><strong>{fmt(r.price,r.symbol==='CNH=X'?4:2)}</strong><small>{r.unit}</small><b className={tone(r.pct)}>{percent(r.pct)}</b></div>
          {r.close_date&&<div className="global-close"><span>美东 {r.close_date.slice(5)} 收盘 {fmt(r.close)}</span><b className={tone(r.close_pct)}>{percent(r.close_pct)}</b></div>}
          <div className="micro">{r.symbol} · {r.market_time?timeText(r.market_time):'等待行情'}{r.delay_minutes!=null? ' · 源标延迟 '+r.delay_minutes+' 分钟':''}</div>
        </article>)}</div>
      </div>)}
      <div className="research-foot">来源 Yahoo Finance / 新浪财经。时间为北京时间，收盘日期按美东交易日。期货为报价源近月连续代码，涨跌按上一日线收价，换月与延时会影响比较。CFD独立列示，按源端前结算/参考价计算涨跌，不替代交易所期货。外盘规则使用有效行情供研判。</div>
    </>}
  </section>;
}
