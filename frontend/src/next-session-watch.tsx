import {useState} from 'react';
import {externalUrl,fmt,money,percent,tone} from './data';
import watchReport from './next-session-watch.json';

const allThemes='全部';
const themeOrder=['影视短剧','人形机器人','端侧 AI','光模块'];
const dateLabel=(date:string)=>Number(date.slice(5,7))+' 月 '+Number(date.slice(8,10))+' 日';
type WatchItem=typeof watchReport.items[number]&{pause?:string};
type WatchActions={
  watch:string[];
  openChart:(code:string)=>void;
  addWatch:(code:string)=>string|null;
};

function WatchCard({item,watched,openChart,onAdd}:{
  item:WatchItem;watched:boolean;openChart:WatchActions['openChart'];onAdd:()=>void;
}){
  return <article className="next-watch-card">
    <div className="next-watch-card-head"><span className="next-watch-theme">{item.theme} · {item.role}</span>
      {item.risk&&<span className="next-watch-risk">{item.risk}</span>}</div>
    <div className="next-watch-stock"><button className="plain" onClick={()=>openChart(item.code)} aria-label={'查看 '+item.name+' 真实走势'}>
      <strong>{item.name}</strong><small>{item.code.slice(2)}</small></button>
      <div><strong>{fmt(item.close)}</strong><b className={tone(item.pct)}>{percent(item.pct)}</b></div></div>
    <div className="next-watch-amount">收盘成交额 {money(item.amount)}</div>
    <p>{item.reason}</p>
    <div className="next-watch-trigger"><b>{item.pause?'买入确认':'盘中确认'}</b><span>{item.trigger}</span>
      {item.pause&&<><b className="next-watch-pause">暂缓</b><span>{item.pause}</span></>}</div>
    <div className="next-watch-actions"><button className="btn" onClick={()=>openChart(item.code)}>真实走势</button>
      <button className="btn" disabled={watched} onClick={onAdd}>{watched?'已自选':'加自选'}</button>
      <a href={externalUrl(item.source)} target="_blank" rel="noreferrer">{item.pause?'公司三季报 ↗':'核对依据 ↗'}</a>
    </div>
  </article>;
}

export function NextSessionWatch({today,watch,openChart,addWatch}:WatchActions&{today?:string}){
  const [theme,setTheme]=useState(allThemes),[feedback,setFeedback]=useState('');
  const localDay=today??new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short'}).format(new Date());
  const status=localDay>watchReport.session?'历史观察':localDay===watchReport.session?'当日观察':'下一交易日';
  const rows=theme===allThemes?watchReport.items:watchReport.items.filter(item=>item.theme===theme);
  const renderCard=(item:WatchItem)=><WatchCard key={item.code} item={item} watched={watch.includes(item.code)}
    openChart={openChart} onAdd={()=>setFeedback(addWatch(item.code)??'已加入自选 · '+item.name)}/>;
  return <section id="tomorrow" className="panel research next-watch">
    <header className="research-head"><div><div className="kicker">05 / NEXT SESSION WATCH</div>
      <h2>{dateLabel(watchReport.session)}关注名单</h2>
      <p>以 {dateLabel(watchReport.as_of)} 收盘行情为基准，整理 {watchReport.items.length} 只热点观察与 {watchReport.consumer.length} 只消费买入候选。</p>
    </div><span className="badge">{status} · {watchReport.session}</span></header>
    <div className="next-watch-context"><span className="kicker">盘面线索</span><p>{watchReport.context}</p></div>
    <section aria-labelledby="next-watch-hot-heading">
      <div className="next-watch-section-head">
        <div><h3 id="next-watch-hot-heading">热点观察 <span>{watchReport.items.length}</span></h3><p>用 09:30 后的走势确认题材承接。</p></div>
        <button className="btn" onClick={()=>document.getElementById('consumer-ideas')?.focus()}>跳到消费买入候选 ↓</button>
      </div>
      <div className="research-bar next-watch-filters"><div className="tabs" role="group" aria-label="热点观察主题">
        {[allThemes,...themeOrder].map(name=><button type="button" key={name} aria-pressed={theme===name}
          className={theme===name?'active':''} onClick={()=>{setTheme(name);setFeedback('')}}>{name} {name===allThemes?watchReport.items.length:watchReport.items.filter(row=>row.theme===name).length}</button>)}
      </div></div>
      <div className="next-watch-grid">{rows.map(renderCard)}</div>
    </section>
    <section id="consumer-ideas" tabIndex={-1} aria-labelledby="next-watch-consumer-heading">
      <div className="next-watch-section-head">
        <div><h3 id="next-watch-consumer-heading">消费买入候选 <span>{watchReport.consumer.length}</span></h3>
        <p>09:45 后逐只核对条件。高开跳过确认价时，等回踩确认再考虑买入。</p></div>
      </div>
      <div className="next-watch-grid">{watchReport.consumer.map(renderCard)}</div>
    </section>
    {feedback&&<div className="research-foot screen-feedback" role="status">{feedback}</div>}
    <div className="research-foot next-watch-sources"><span>行情基准 {watchReport.as_of} 收盘 · 腾讯财经</span>
      {watchReport.sources.map(source=><a key={source.url} href={externalUrl(source.url)} target="_blank" rel="noreferrer">{source.name} ↗</a>)}</div>
  </section>;
}
