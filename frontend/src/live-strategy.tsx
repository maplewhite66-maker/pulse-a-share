import {fmt,percent,timeText,tone,useFeed,type Quote,type Alert} from './data';

type AuctionFeed={source:string;status:string;provider_response_at?:string;items?:{
  code:string;market_time:string;indicative_price:number;matched_volume:number;unmatched_volume:number}[]};

type State={day:string;phase:string;stage:string;active:boolean;heartbeat:string;started_at?:string;last_scan_at?:string;
  last_success_at?:string;scans?:number;watched?:number;valid_quotes?:number;last_error?:string;calendar_error?:string;
  strategy:{label:string;action:string;coverage:number;index_mean_pct?:number};quotes?:Quote[];alerts?:Alert[];
  auction?:{at:string;status:string;verified_quotes:number;tradable_signals:unknown[];feed?:AuctionFeed};
  sectors?:{items:{code:string;name:string;main_net:number;pct:number}[];is_stale:boolean;error?:string;note?:string}};
type Monitor={enabled:boolean;running:boolean;planned_start:string;timezone:string;quote_interval_s:number;sector_interval_s:number;
  scope:string;state:State|null;auction_note:string;auction_permission?:string;execution:string;schedule:{time:string;stage:string;action:string}[]};

const auctionStatus:Record<string,string>={unconfigured:'等待授权凭证',restricted:'未授权在公网展示',
  ready:'等待开盘竞价核验',outside_window:'虚拟撮合时段已结束',not_ready:'源端尚未提供有效快照',
  unavailable:'供应商暂不可用',stale:'供应商响应时间滞后',source_time_unverified:'缺少可核验的源端时标',
  verified_snapshot:'源端时间已核验',historical_snapshot:'本交易日竞价留档'};

function AuctionData({monitor}:{monitor:Monitor|null|undefined}){
  const auction=monitor?.state?.auction?.feed;
  const status=auction?.status??monitor?.auction_permission;
  return <div className="ideas-rule">
    <b>开盘集合竞价 · 虚拟撮合</b>
    <p>{status?auctionStatus[status]??status:'等待服务状态'}。{monitor?.auction_note}</p>
    {!!auction?.items?.length&&<div className="table-wrap research-table"><table>
      <thead><tr><th>股票</th><th>虚拟匹配价</th><th>匹配量</th><th>未匹配量</th><th>行情时间</th></tr></thead>
      <tbody>{auction.items.map(row=><tr key={row.code}><td>{row.code}</td>
        <td>{fmt(row.indicative_price)}</td><td>{fmt(row.matched_volume,0)}</td>
        <td>{fmt(row.unmatched_volume,0)}</td><td>{timeText(row.market_time)}</td></tr>)}</tbody>
    </table></div>}
    {!!auction?.items?.length&&<small>来源 {auction.source} · 数量采用供应商原值</small>}
  </div>;
}

export function AuctionWatch({visible,revision}:{visible:boolean;revision:number}){
  const feed=useFeed<Monitor>(visible?'/api/monitor/status':null,'live',8000,revision);
  if(!visible)return null;
  const m=feed?.data,s=m?.state;
  return <div className="ideas-rule cloud-monitor-summary"><div><b>{m?.enabled?'云端自动盯盘已启用':'云端盯盘未启用'}</b>
    <span> · {s?.stage??'等待服务状态'} · {m?.planned_start??'交易日09:15'}</span></div>
    <p>{s?.strategy?.action??'09:15开始竞价观察，09:30开始开盘量价确认。'}</p>
    {s?.started_at&&<small>实际开始 {timeText(s.started_at)} · 有效报价 {s.valid_quotes??0}/{s.watched??0} · 最近扫描 {timeText(s.last_scan_at)}</small>}
    {(s?.last_error||s?.calendar_error||feed?.error)&&<p className="report-warning">{s?.last_error??s?.calendar_error??feed?.error}</p>}
    {(s?.phase==='open_auction'||s?.phase==='pause')&&<AuctionData monitor={m}/>}
  </div>;
}

export function LiveStrategy({revision,openChart,openFlow,onIdeas}:{revision:number;openChart:(c:string)=>void;openFlow:(c:string)=>void;onIdeas:()=>void}){
  const feed=useFeed<Monitor>('/api/monitor/status','live',8000,revision);
  const events=useFeed<Alert[]>('/api/monitor/events','live',15000,revision);
  const m=feed?.data,s=m?.state;
  return <section id="monitor" className="research panel">
    <header className="research-head global-head"><div><div className="kicker">PULSE / LIVE STRATEGY</div>
      <h2>从集合竞价，到收盘复核。</h2><p>云端常驻观察 · 09:15启动 · 网页关闭后继续扫描</p></div><button className="btn" onClick={onIdeas}>查看开收盘候选 ↗</button></header>
    <div className="paper-metrics">
      <article><span>服务状态</span><strong>{m?.enabled&&m?.running?'正在运行':m?.enabled?'启动中':'未启用'}</strong><small>{m?.planned_start??'正在连接服务'}</small></article>
      <article><span>交易阶段</span><strong>{s?.stage??'等待状态'}</strong><small>{s?.heartbeat?timeText(s.heartbeat):'—'}</small></article>
      <article><span>有效报价</span><strong>{s?.valid_quotes??0} / {s?.watched??0}</strong><small>常规周期 {m?.quote_interval_s??8} 秒</small></article>
      <article><span>本交易日扫描</span><strong>{s?.scans??0} 次</strong><small>实际启动 {s?.started_at?timeText(s.started_at):'尚未开始'}</small></article>
    </div>
    <div className="ideas-rule"><b>{s?.strategy?.label??'等待交易时段'}</b><p>{s?.strategy?.action??feed?.error??'核对官方交易日历后开始扫描。'}</p>
      <small>最近扫描 {s?.last_scan_at?timeText(s.last_scan_at):'—'} · 最近有效采集 {s?.last_success_at?timeText(s.last_success_at):'—'}</small>
      {(s?.last_error||s?.calendar_error)&&<p className="report-warning">{s.last_error??s.calendar_error}</p>}
    </div>
    <AuctionData monitor={m}/>
    <div className="research-bar"><strong>全天执行节奏</strong><span className="muted">时间均为北京时间；休市暂停报价扫描</span></div>
    <div className="table-wrap research-table"><table><thead><tr><th>时段</th><th>阶段</th><th>运行内容</th></tr></thead><tbody>
      {m?.schedule.map(row=><tr key={row.time}><td>{row.time}</td><td>{row.stage}</td><td>{row.action}</td></tr>)}
    </tbody></table></div>
    <div className="research-bar"><strong>云端观察池</strong><span className="muted">{m?.scope}</span></div>
    <div className="table-wrap research-table"><table><thead><tr><th>股票</th><th>主力资金</th><th>最新报价</th><th>涨跌幅</th><th>源端时间</th><th>状态</th></tr></thead><tbody>
      {(s?.quotes??[]).map(q=><tr key={q.code}><td><button className="plain row-link" onClick={()=>openChart(q.code)}>{q.name}<small>{q.code}</small></button></td>
        <td><button className="plain row-link" onClick={()=>openFlow(q.code)}>查看资金</button></td>
        <td>{fmt(q.price)}</td><td className={tone(q.pct)}>{percent(q.pct)}</td><td>{timeText(q.market_time)}</td><td>{q.is_stale?'失效/滞后，不用于信号':'报价有效'}</td></tr>)}
    </tbody></table></div>
    {!s?.quotes?.length&&<div className="empty">尚无有效扫描记录，数据源失败时不会补造行情。</div>}
    <div className="research-bar"><strong>云端异动记录</strong><span className="muted">独立于手机页面的扫描记录</span></div>
    <div className="paper-records">{events?.data?.slice(0,15).map(a=><article className="paper-record" key={a.id}><b>{a.name} · {a.event}</b><p>{fmt(a.value)} {a.unit} · {timeText(a.market_time)}</p></article>)}</div>
    {!events?.data?.length&&<div className="empty">暂无通过规则校验的云端异动。</div>}
    <div className="research-foot">{m?.auction_note}。{m?.execution}。策略标签是指数与量价规则推断，不是胜率预测。手机本地自选需另行同步才会进入云端池。</div>
  </section>;
}
