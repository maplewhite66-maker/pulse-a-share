import {useEffect,useState} from 'react';
import {externalUrl,fmt,money,percent,timeText,tone,useFeed,type Clock,type Mode,type Num,type Part} from './data';
import {AuctionWatch} from './live-strategy';
import {ResearchSource} from './research';
import type {Tracked} from './tracking';

type Session='open'|'tail';
type Tab=Session|'archive';
type Pick={code:string;name:string;price:number;pct:number;amount:number;vol_ratio:number;open:number;score:number;parts:Part[];
  entry_ref?:number;entry_cap?:number;invalid_below?:number;main_net:Num;tail_move:Num;tail_amount:Num;market_time:string;pool_time:string|null;baseline_time:string|null};
type Candidates={session:Session;window:string;status:string;items:Pick[];pool_size:number;checked:number;waiting:number;
  pool_source:string|null;pool_url:string|null;pool_time:string|null;pool_note:string|null};
type Archived={day:string;status:string;ranking:(Candidates&{at:string|null})|null};
type CloseWatch={code:string;name:string;price:number;pct:Num;invalid_below:number;market_time:string;score:number};
type Daily={day:string;open?:Archived;tail?:Archived;close?:{at:string;statuses:Record<Session,string>;items:CloseWatch[];checked:number}};
const definitions={
  open:{name:'开盘候选',time:'09:30–10:00',pool:'成交额榜首 100 项',rule:'当日涨幅 0.8%–6.5%，量比至少 1.3，成交额至少 0.3 亿，最新价不低于开盘价。'},
  tail:{name:'尾盘候选',time:'14:45–14:57',pool:'主力净额榜首 100 项',rule:'当日涨幅 0.8%–6.5%，量比至少 1，成交额至少 1.5 亿，价格不低于开盘价，榜单主力净额为正。尾盘两次有效报价之间须上涨至少 0.12%，成交额增加至少 200 万元。'}
};
const messages:Record<string,string>={
  closed:'当前不在本策略的扫描时段。',calendar:'交易日历待确认，正在等待时段核验。',loading:'正在采集当日榜单…',
  unavailable:'数据源暂不可用，等候下一次刷新。',stale:'榜单行情时间不足以确认当日候选。',
  empty:'当前没有通过全部条件的标的。',sampling:'正在积累尾盘报价与成交额快照。',
  ready:'候选已按规则分排序。',history:'当日历史记录，适用窗口已结束或行情待确认。'
};

export function BuyCandidates({mode,clock,visible,revision,watch,tracked,openChart,addWatch,addTrack}:{
  mode:Mode;clock:Clock|null|undefined;visible:boolean;revision:number;watch:string[];tracked:Tracked[];
  openChart:(c:string)=>void;addWatch:(c:string)=>string|null;addTrack:(c:string)=>string|null;
}){
  const [tab,setTab]=useState<Tab>('open'),[feedback,setFeedback]=useState(''),[archiveDay,setArchiveDay]=useState('');
  const hm=clock?.time.slice(11,16)??'';
  const activeOpen=mode==='live'&&clock?.phase==='morning'&&hm>='09:30'&&hm<'10:00';
  const activeTail=mode==='live'&&clock?.phase==='afternoon'&&hm>='14:45'&&hm<'14:57';
  const opening=useFeed<Candidates>(visible&&tab==='open'?'/api/stocks/buy-candidates?session=open':null,
    mode,activeOpen||mode==='demo'?8000:15000,revision);
  const tail=useFeed<Candidates>(visible&&tab==='tail'?'/api/stocks/buy-candidates?session=tail':null,
    mode,activeTail||mode==='demo'?8000:15000,revision);
  const history=useFeed<Daily[]>(visible&&tab==='archive'?'/api/stocks/session-journal':null,mode,30_000,revision);
  const days=history?.data??[],day=days.find(d=>d.day===archiveDay)??days[0];
  const packet=tab==='open'?opening:tail,data=packet?.data,desc=definitions[tab==='open'?'open':'tail'];
  const outside=mode==='live'&&(tab==='open'?!activeOpen:!activeTail);
  const historical=Boolean(data?.items.length&&(outside||packet?.is_stale||data.status==='history'));
  useEffect(()=>{setFeedback('')},[mode,tab]);
  return <section id="ideas" className="panel research" hidden={!visible}>
    <header className="research-head"><div><div className="kicker">04 / SESSION CANDIDATES</div><h2>竞价观察与开收盘候选</h2>
      <p>开盘找承接，尾盘找量价同步，收盘后复核次日观察名单。</p></div>
      <span className="badge">{mode==='demo'?'离线演示':clock?.label??'等待时段确认'}</span></header>
    <AuctionWatch visible={visible&&mode==='live'&&tab==='open'} revision={revision}/>
    <div className="research-bar ideas-controls">
      <div className="tabs" role="group" aria-label="候选时段">{(['open','tail','archive'] as const).map(key=>
        <button key={key} type="button" className={tab===key?'active':''} aria-pressed={tab===key} onClick={()=>setTab(key)}>{key==='archive'?'收盘复核 / 档案':definitions[key].name}</button>)}</div>
      <span>{tab==='archive'?'交易日 15:05 后复核':desc.time+' 北京时间 · '+desc.pool}</span>
    </div>
    {tab==='archive'?<div className="session-archive">
      <div className="ideas-rule">收盘复核只检查当日尾盘榜。收盘价须在尾盘失效线与参考上限之间，次日还需重新验证开盘承接。</div>
      {mode==='demo'?<div className="empty">档案使用真实行情。切换至真实模式查看。</div>:day?<>
        <div className="archive-toolbar"><label>交易日 <select value={day.day} onChange={e=>setArchiveDay(e.target.value)}>{days.map(d=><option key={d.day}>{d.day}</option>)}</select></label>
          <span>{day.close?'已归档 '+timeText(day.close.at):'等待收盘复核'}</span></div>
        <div className="session-cards">{(['open','tail'] as const).map(key=>{
          const slot=day[key],rank=slot?.ranking;
          return <div key={key} className="session-card"><h3>{definitions[key].name}</h3>
            <p>{slot?(messages[slot.status]??slot.status):'这个时段没有采集记录。'}</p>
            {rank&&<><p>最近有效排名 · {timeText(rank.at)}</p><ul>{rank.items.map(r=><li key={r.code}>
              <button className="plain" onClick={()=>openChart(r.code)}>{r.name} {r.code.slice(2)}</button>
              <span>¥{fmt(r.price)} · {fmt(r.score,1)} 分</span></li>)}</ul></>}
          </div>
        })}</div>
        <div className="next-watch-section-head"><div><h3>次日开盘观察</h3><p>{day.close?'复核尾盘候选 '+day.close.checked+' 只。':'等服务完成收盘价复核。'}</p></div></div>
        {day.close?.items?.length?<div className="table-wrap research-table"><table><thead><tr><th>股票</th><th>收盘价</th><th>尾盘失效参考</th><th>收盘行情时间</th><th>次日触发条件</th></tr></thead><tbody>
          {day.close.items.map(r=><tr key={r.code}><td><button className="plain row-link" onClick={()=>openChart(r.code)}>{r.name}<small>{r.code.slice(2)}</small></button></td>
          <td>{fmt(r.price)}</td><td>{fmt(r.invalid_below)}</td><td>{timeText(r.market_time)}</td><td>守住失效参考，回踩后收复当日分时均价，板块同步回升</td></tr>)}
        </tbody></table></div>:<div className="empty">{day.close?'没有通过收盘价复核的候选。':'暂无次日观察名单。'}</div>}
      </>:<div className="empty">{history?.error??(history?'暂无真实候选档案，开盘后由云端服务自动采集。':'正在读取档案…')}</div>}
    </div>:<>
    <div className="ideas-rule">{desc.rule} 排除 ST 和退市整理，每次最多用最新报价复核 32 只。</div>
    {data?.pool_source&&<div className="source">
      <span>初筛 {data.pool_url?<a href={externalUrl(data.pool_url)} target="_blank" rel="noreferrer">{data.pool_source} ↗</a>:data.pool_source}</span>
      <span>行情 {timeText(data.pool_time)}</span>{data.pool_note&&<span>{data.pool_note}</span>}
    </div>}
    <div className="screen-summary" aria-live="polite"><strong>{historical?'当日历史记录':data?.items.length?`当前候选 ${data.items.length} 只`:messages[data?.status??'loading']}</strong>
      {data&&<span>榜单 {data.pool_size} · 复核池 {data.checked}{data.waiting? ` · 等待第二次快照 ${data.waiting}`:''}</span>}</div>
    {data?.items.length?<div className="table-wrap research-table"><table><thead><tr><th>股票 / 报价时间</th><th>触发价 / 失效参考</th><th>涨跌幅</th><th>成交额</th><th>量比</th>
      {tab==='tail'&&<><th>尾盘涨跌</th><th>尾盘成交额增量</th><th>榜单主力净额</th></>}<th>规则分</th><th>操作</th></tr></thead>
      <tbody>{data.items.map(r=><tr key={r.code}>
        <td><button className="plain row-link" onClick={()=>openChart(r.code)}>{r.name}<small>{r.code.toUpperCase()}</small></button><div className="micro">{timeText(r.market_time)}</div></td>
        <td>{fmt(r.price)}{!historical&&r.entry_cap!=null&&<div className="micro">上限 {fmt(r.entry_cap)} · 失效 &lt; {fmt(r.invalid_below)}</div>}</td><td className={tone(r.pct)}>{percent(r.pct)}</td><td>{money(r.amount)}</td><td>{fmt(r.vol_ratio)} 倍</td>
        {tab==='tail'&&<><td className={tone(r.tail_move)}>{percent(r.tail_move)}</td><td>{money(r.tail_amount)}</td><td>{money(r.main_net,true)}</td></>}
        <td><details className="explain idea-score"><summary>{fmt(r.score,1)} 分</summary>{r.parts.map(p=>
          <div key={p.label} className="micro">{p.label} {fmt(p.score,1)} × {p.weight}%</div>)}
          {r.baseline_time&&<div className="micro">尾盘对比基准 {timeText(r.baseline_time)}</div>}
          <div className="micro">初筛时间 {timeText(r.pool_time)}</div><div className="micro">开盘价 {fmt(r.open)}</div></details></td>
        <td className="screen-row-actions"><button className="btn" disabled={watch.includes(r.code)}
          onClick={()=>setFeedback(addWatch(r.code)??'已加入自选 · '+r.name)}>{watch.includes(r.code)?'已自选':'加自选'}</button>
          <button className="btn" disabled={tracked.some(t=>t.code===r.code)}
            onClick={()=>setFeedback(addTrack(r.code)??'已加入追踪 · '+r.name)}>{tracked.some(t=>t.code===r.code)?'已追踪':'追踪'}</button></td>
      </tr>)}</tbody></table></div>:<div className="empty research-empty">{messages[data?.status??'loading']}</div>}
    {feedback&&<div className="research-foot screen-feedback" role="status">{feedback}</div>}
    <ResearchSource packet={packet}/>
    <div className="research-foot">{mode==='demo'?'所有候选和对比快照均为演示样本。':'规则分只衡量当前筛选条件。'} 尾盘窗口在收盘集合竞价前结束。参考上限是触发价上浮 0.3%，跌破开盘价或尾盘对比基准后需重新确认。</div>
    </>}
  </section>;
}
