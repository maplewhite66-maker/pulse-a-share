import {useState} from 'react';
import {externalUrl,fmt,percent,timeText,tone,useFeed} from './data';

type WorldQuote={symbol:string;name:string;group:string;unit:string;price?:number;pct?:number;market_time?:string;
  close_date?:string;close_pct?:number;status:string;url:string;error?:string;delay_minutes?:number};
type Candidate={code:string;name:string;sector:string;score:number;price:number;pct:number;return5:number;as_of:string;
  reason:string;trigger:string;risk:string;invalid_below:number;source_url:string};
type Report={revision?:number;provenance?:string;day:string;title:string;generated_at:string;status:string;late:boolean;prior_day:string;scheduled_for:string;
  coverage:{available:number;total:number;quote_count:number;sample_size:number};
  global:{rows:WorldQuote[];coverage:number;total:number;stance:string};
  news:{status:string;error?:string;source:string;data?:{items:{id:string;title:string;time:string;url:string}[];complete_window:boolean}};
  impacts:{theme:string;direction:string;sectors:string;evidence:string;trigger:string;source_url?:string}[];
  candidates:Candidate[];selection:string;risk:string[];method:string;markdown:string;sha256:string;
  sectors:{status:string;market_time?:string;error?:string;note?:string;items:{code:string;name:string;main_net:number;pct?:number}[]}};
type Status={enabled:boolean;timezone:string;schedule:string;running:boolean;last_attempt?:string;last_error?:string};
const states:Record<string,string>={complete:'完整报告',partial:'部分数据可用',unavailable:'数据未就绪',available:'可用',missing:'缺失',stale:'滞后'};

export function Premarket({revision,openChart}:{revision:number;openChart:(code:string)=>void}){
  const [day,setDay]=useState('latest');
  const feed=useFeed<Report>('/api/reports/'+day,'live',60_000,revision);
  const archive=useFeed<Pick<Report,'day'|'generated_at'|'status'>[]>('/api/reports','live',60_000,revision);
  const status=useFeed<Status>('/api/reports/status','live',30_000,revision);
  const report=feed?.data;
  const schedule=status?.data;
  const renderWorld=(rows:WorldQuote[])=><div className="report-world-grid">{rows.map(q=><article key={q.symbol} className="report-world">
    <div><a href={externalUrl(q.url)} target="_blank" rel="noreferrer">{q.name} ↗</a><span className={'market-state '+q.status}>{states[q.status]??q.status}</span></div>
    <div><strong>{fmt(q.price)}</strong><small>{q.unit}</small><b className={tone(q.pct)}>{percent(q.pct)}</b></div>
    <small>{q.market_time?timeText(q.market_time):q.error??'等待来源返回'}</small>
    {q.close_date&&<small>已收盘 {q.close_date} · <b className={tone(q.close_pct)}>{percent(q.close_pct)}</b></small>}
    {q.delay_minutes!=null&&<small>源端标注延迟 {q.delay_minutes} 分钟</small>}
  </article>)}</div>;
  return <section className="research panel premarket" id="premarket">
    <header className="research-head report-hero"><div><div className="kicker">PULSE / MORNING BRIEF</div>
      <h2>开盘之前，先看全局。</h2><p>隔夜市场 · 国际事件 · 行业线索 · A股观察池</p>
      <div className="report-schedule"><i className={schedule?.enabled?'on':''}/>{schedule?.enabled?'交易日 08:00 自动生成':'定时生成尚未启用'} · 北京时间</div>
    </div><div className="report-actions"><label>报告日期<select aria-label="报告日期" value={day} onChange={e=>setDay(e.target.value)}>
      <option value="latest">最新报告</option>{archive?.data?.map(r=><option value={r.day} key={r.day}>{r.day}</option>)}</select></label>
      {report&&<a className="btn report-export" href={'/api/reports/'+report.day+'.md'} download>导出 Markdown ↓</a>}
    </div></header>
    {!report?<div className="report-pending"><span className="kicker">REPORT STATUS</span><h3>{schedule?.running?'正在采集和生成盘前报告':'等待首份盘前报告'}</h3>
      <p>{schedule?.last_error??feed?.error??'每个A股交易日08:00开始采集。生成后可在这里阅读和导出。'}</p>
      <small>官方日历未确认时等待重试。08:00后恢复服务会在09:15前补生成，并显示实际时间。</small></div>:
    <div className="report-body">
      <div className="report-summary"><div><span className="kicker">{report.day} / A-SHARE</span><h3>{report.global.stance}</h3>
        <p>{states[report.status]} · 最近完成的A股交易日 {report.prior_day}</p></div>
        <div className="report-coverage"><strong>{report.coverage.available}<small> / {report.coverage.total}</small></strong><span>可用数据项</span></div></div>
      <div className={'report-stamp '+(report.late?'report-warning':'')}>
        <span>生成 {timeText(report.generated_at)} · 北京时间</span>
        {report.late&&<b>08:05后补生成 · 数据截至实际生成时点</b>}
        <span>缺失、滞后数据不参与方向判断；报告归档后不覆盖。版本 {report.revision??1}。</span>
        {report.provenance&&<span>{report.provenance}</span>}
      </div>
      <section><h3><em>01</em> 隔夜美股 <span>收盘日期单独核对</span></h3>{renderWorld(report.global.rows.filter(q=>q.group==='us'))}</section>
      <section><h3><em>02</em> 全球期货、CFD与宏观 <span>CFD独立标注 · 可能有延迟</span></h3>{renderWorld(report.global.rows.filter(q=>q.group!=='us'))}</section>
      <section><h3><em>03</em> 国际新闻与事件</h3><div className="report-news">
        {(report.news.data?.items??[]).map(item=><article key={item.id}><time>{timeText(item.time)}</time><a href={externalUrl(item.url)} target="_blank" rel="noreferrer">{item.title} ↗</a></article>)}
        {report.news.status!=='available'&&<p className="report-warning">新闻{states[report.news.status]}：{report.news.error??'请等待来源恢复'}</p>}
      </div><p className="report-note">来源：{report.news.source??'暂未取回'}；最近最多400条全球快讯中的国际与行业关键词匹配。{report.news.data&&!report.news.data.complete_window?'未完整覆盖隔夜窗口。':''}</p></section>
      <section><h3><em>04</em> 行业影响 <span>规则推断</span></h3><div className="report-impact-grid">{report.impacts.map(item=><article key={item.theme}>
        <div><h4>{item.theme}</h4><span className="badge">{item.direction}</span></div><p>{item.sectors}</p>
        <small>{item.source_url?<a href={externalUrl(item.source_url)} target="_blank" rel="noreferrer">{item.evidence} ↗</a>:item.evidence}</small><p className="report-condition">{item.trigger}</p>
      </article>)}</div></section>
      <section><h3><em>05</em> A股机会观察池 <span>{report.candidates.length} 只通过</span></h3><p className="report-note">{report.selection}</p>
        <div className="report-picks">{report.candidates.map(p=><article key={p.code}>
          <div className="report-pick-head"><div><button onClick={()=>openChart(p.code)}>{p.name} ↗</button><small>{p.code.toUpperCase()} · {p.sector}</small></div><div><strong>{fmt(p.price)}</strong><b className={tone(p.pct)}>{percent(p.pct)}</b></div></div>
          <p>{p.reason}</p><dl><div><dt>确认条件</dt><dd>{p.trigger}</dd></div><div><dt>失效参考</dt><dd>前日低点 {fmt(p.invalid_below)}。{p.risk}</dd></div></dl>
          <small>行情 {timeText(p.as_of)} · 规则分 {p.score} / 100</small>
        </article>)}</div>{!report.candidates.length&&<div className="empty">没有通过时间和技术条件校验的标的。保留空观察池。</div>}</section>
      <section><h3><em>06</em> 板块资金TOP20 <span>主力净额</span></h3>
        {report.sectors.status!=='available'&&<p className="report-note report-warning">{report.sectors.items.length?'上游读取失败，以下仅为此前真实缓存，不代表当日实时资金。':'当日主力净额尚未得到有效确认。'}</p>}
        <div className="report-sector-list">{report.sectors.items.map((s,i)=><div key={s.code}><small>{String(i+1).padStart(2,'0')}</small><b>{s.name}</b><span className={tone(s.main_net)}>{fmt(s.main_net/1e8)} 亿</span><span className={tone(s.pct)}>{percent(s.pct)}</span></div>)}</div>
        {!report.sectors.items.length&&<div className="empty">板块资金接口暂无可用数据</div>}
        <p className="report-note">{report.sectors.market_time?timeText(report.sectors.market_time):'上游未提供行情时间，日期未确认'} · {report.sectors.error??report.sectors.note}</p>
      </section>
      <section className="report-risks"><h3><em>07</em> 风险与执行条件</h3><ul>{report.risk.map(r=><li key={r}>{r}</li>)}</ul></section>
      <footer><p>{report.method}</p><details><summary>查看 Markdown 原文与校验值</summary><small>SHA-256 {report.sha256}</small><pre>{report.markdown}</pre></details></footer>
    </div>}
  </section>;
}
