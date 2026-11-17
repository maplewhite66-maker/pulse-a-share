import {useState} from 'react';
import {externalUrl,fmt,timeText,useFeed,type Mode} from './data';

type Candidate={code:string;name:string;quote:number;quote_time:string;signal_price:number;entry_floor:number;entry_cap:number;
  wins:number;samples:number;win_rate:number|null;wilson_lower:number|null;covered_days:number;unresolved:number;
  historical_signals:number;mean_net:number|null;reason?:string};
type Round={slot:string;status:'ready'|'empty'|'unavailable'|'review';items:Candidate[];checks?:Candidate[];
  pool_size?:number;checked?:number;entry_at?:string;diagnostics?:Record<string,number>;rounds?:number;qualified?:number;
  pool_source?:string|null;history_source?:string|null;pool_time?:string|null;error?:string|null;
  backtest_from?:string;backtest_to?:string};
type Feed={rounds:Round[];running:string|null};
const pct=(value:number|null)=>value==null?'—':fmt(value*100,1)+'%';

export function HourlyOpportunities({mode,revision,openChart,openPlan}:{
  mode:Mode;revision:number;openChart:(code:string)=>void;openPlan:(code:string,price:number)=>void;
}){
  const data=useFeed<Feed>('/api/stocks/hourly-opportunities',mode,15000,revision);
  const [selection,setSelection]=useState('');
  const rounds=data?.data?.rounds??[];
  const current=rounds.find(r=>r.slot===selection)??rounds[0];
  const active=Boolean(!data?.is_stale&&current?.entry_at&&Date.now()<Date.parse(current.entry_at));
  const counts=current?.diagnostics??{};
  return <section id="hourly" className="panel research">
    <header className="research-head"><div><div className="kicker">07 / HOURLY OPPORTUNITIES</div><h2>超短线机会筛选</h2>
      <p>北京时间 10:30、11:30、14:00 扫描，15:00 汇总。历史胜率须严格高于 80%，至少 30 笔模拟成交。</p></div>
      <span className="badge">{mode==='demo'?'离线演示':active?'当前时段':'历史或待扫描'}</span></header>
    <div className="research-bar ideas-controls">
      {rounds.length>0&&<label>扫描批次<select value={selection||''} onChange={e=>setSelection(e.target.value)}>
        <option value="">最新一轮</option>{rounds.map(row=><option key={row.slot} value={row.slot}>{timeText(row.slot)}{row.status==='review'?' · 收盘汇总':''}</option>)}
      </select></label>}
      <span>{data?.data?.running?'正在扫描 '+timeText(data.data.running):'本地服务自动执行，页面每 15 秒读取结果。'}</span>
    </div>
    {data?.error&&<div className="research-foot">{data.error}</div>}
    {mode==='demo'?<div className="empty">切换到「真实」查看小时扫描结果。</div>:
      !current?<div className="empty">{data?'暂无交易时段扫描记录。请在交易时段保持本地服务运行。':'正在读取扫描记录…'}</div>:
      current.status==='review'?<div className="screen-summary"><strong>收盘汇总</strong>
        <span>{timeText(current.slot)} · 已记录 {current.rounds} 轮 · 达标记录 {current.qualified} 条</span></div>:<>
      <div className="screen-summary"><strong>{current.items.length} 只通过</strong>
        <span>{timeText(current.slot)} · 原榜单 {current.pool_size} · 复核 {current.checked} · 有效分时 {counts.available??0}</span>
        <span>模拟入场核对 {timeText(current.entry_at)}{active?'':' · 本轮已结束'}</span>
        {current.backtest_from&&<span>验证 {current.backtest_from} 至 {current.backtest_to}</span>}
        <span>行情缺失 {(counts.quote_missing??0)+(counts.bars_missing??0)} · 无形态 {counts.no_setup??0} · 数据/样本不足 {counts.insufficient??0} · 胜率不足 {counts.threshold??0}</span>
      </div>
      {current.error&&<div className="research-foot">{current.error}</div>}
      {current.items.length>0?<div className="table-wrap"><table className="research-table">
        <thead><tr><th>标的</th><th>核对报价</th><th>模拟入场区间</th><th>历史胜率</th><th>胜出／样本</th><th>95% 区间下限</th><th>已知样本平均净收益</th><th>操作</th></tr></thead>
        <tbody>{current.items.map(row=><tr key={row.code}>
          <td><strong>{row.name}</strong> <small>{row.code.slice(2)}</small></td><td>{fmt(row.quote)} <small>{row.quote_time.slice(11,19)}</small></td>
          <td>{fmt(row.entry_floor)}–{fmt(row.entry_cap)}</td><td>{pct(row.win_rate)}</td><td>{row.wins}/{row.samples}</td>
          <td>{pct(row.wilson_lower)}</td><td>{pct(row.mean_net)}</td>
          <td><button className="btn" onClick={()=>openChart(row.code)}>走势</button>{' '}
            <button className="btn" disabled={!active} onClick={()=>openPlan(row.code,row.entry_cap)}>写入计划草稿</button></td>
        </tr>)}</tbody>
      </table></div>:<div className="empty">{current.status==='unavailable'?'本轮未取得可用行情。':'本轮没有达到全部门槛的机会。'}</div>}
      {!!current.checks?.length&&<details className="research-foot hourly-explain"><summary>查看未达标的历史验证</summary>
        <div className="table-wrap"><table className="research-table"><thead><tr><th>标的</th><th>交易日覆盖</th><th>触发</th><th>模拟成交</th><th>未确认退出</th><th>历史胜率</th><th>原因</th></tr></thead>
          <tbody>{current.checks.map(row=><tr key={row.code}><td>{row.name} {row.code.slice(2)}</td><td>{row.covered_days}/100</td>
            <td>{row.historical_signals}</td><td>{row.samples}</td><td>{row.unresolved}</td><td>{pct(row.win_rate)}</td>
            <td>{row.reason==='insufficient'?'样本或覆盖不足':'胜率未过 80%'}</td></tr>)}</tbody></table></div>
      </details>}
      <div className="research-foot next-watch-sources">
        {current.pool_source&&<a href={externalUrl(current.pool_source)} target="_blank" rel="noreferrer">东方财富初筛 ↗</a>}
        {current.pool_time&&<span>初筛行情 {timeText(current.pool_time)}</span>}
        {current.history_source&&<a href={externalUrl(current.history_source)} target="_blank" rel="noreferrer">新浪 30 分钟行情 ↗</a>}
      </div>
    </>}
    <details className="research-foot hourly-explain" open><summary>筛选与历史胜率口径</summary>
      <p>以成交额前 100 榜单为初筛池，最多复核 20 只成交额至少 1 亿元的沪深主板非 ST 股票。使用收完的 30 分钟线，要求较昨收涨 0.3%–5%，价格高于当日开盘、量价均价和本根开盘，当前成交量至少为前一根的 0.8 倍。</p>
      <p>对同一股票同一时段，在最近 100 个已有完整次交易日的交易日重放规则。下一根 30 分钟线收盘价至少为信号价的 99%，计入滑点后买价至多高于信号价 0.5%。下一交易日 10:00 收盘价退出，扣约 0.30% 双边费用与滑点后收益为正记一胜。历史窗口与已成交退出各须至少 90% 覆盖，无法确认退出计为未胜出。显示 Wilson 95% 区间下限供比较。历史命中率随市场和成交条件变化。</p>
    </details>
  </section>;
}
