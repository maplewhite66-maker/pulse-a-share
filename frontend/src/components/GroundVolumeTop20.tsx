import {useState} from 'react';
import {fmt, percent, timeText, tone, useFeed, type Mode} from '../data';
import {sortGroundVolume, type GroundSort} from '../ground-volume-sort';

type Metrics = {samples:number;win_rate:number|null;mean_return:number|null;payoff_ratio:number|null};
type Row = {
  rank:number;code:string;name:string;sector:string;price:number;change_pct:number;turnover:number;quote_at:string;
  volume_percentile:number|null;volume_vs_60d_peak:number|null;volume_5_60_ratio:number|null;
  win_rate:number;mean_return:number;max_drawdown:number;payoff_ratio:number|null;score:number;signals:string[];
  entry:[number,number];s1:number;s2:number;stop:number;support:number;max_position_pct:number;
  history:{trigger_count:number;covered_days:number;window_days:number;horizons:Record<'1'|'3'|'5',Metrics>};
};
type Result = {
  day:string;slot:string;as_of:string;source:string;items:Row[];scanned:number;qualified:number;
  backtest_sessions:number;diagnostics:Record<string,number>;
};
type Snapshot = {
  status:'ready'|'waiting'|'running'|'not_configured'|'input_unavailable'|'live_only';
  stale:boolean;result:Result|null;expected_slot:string|null;error:string|null;
};
type Slot = 'latest'|'14:45'|'15:15';
const sortLabels: [GroundSort,string][] = [
  ['score','综合得分'],['win_rate','历史胜率'],['mean_return','预期盈利能力'],['max_drawdown','最小回撤'],
];
const statusText: Record<Snapshot['status'],string> = {
  ready:'本时点已归档', waiting:'等待本时点快照或交易日历确认', running:'正在筛选',
  not_configured:'尚未配置授权数据快照', input_unavailable:'本时点数据不可用', live_only:'请切换到真实模式',
};
const ratioText = (value:number|null) => value == null ? '—' : fmt(value * 100,1) + '%';

export function GroundVolumeTop20({mode, revision, openChart, addWatch, addTrack}: {
  mode:Mode; revision:number;
  openChart:(code:string, view:'line'|'kline')=>void;
  addWatch:(code:string)=>string|null; addTrack:(code:string)=>string|null;
}) {
  const [slot,setSlot] = useState<Slot>('latest');
  const [sort,setSort] = useState<GroundSort>('score');
  const [feedback,setFeedback] = useState('');
  const packet = useFeed<Snapshot>('/api/strategy/ground-volume-top20?slot='+encodeURIComponent(slot),mode,30_000,revision);
  const payload = packet?.data;
  const result = payload?.result;
  const rows = sortGroundVolume(result?.items ?? [],sort);
  function follow(row: Row) {
    const watch = addWatch(row.code);
    const track = addTrack(row.code);
    setFeedback(`${row.name} · 自选：${watch ?? '已加入'} · 追踪：${track ?? '已加入'}`);
  }
  return <section id="ground-volume" className="panel research">
    <header className="research-head">
      <div><h2>地量准备启动 · TOP 20</h2>
        <p>14:45 盘中预筛 · 15:15 收盘确认。按完整条件筛选，合格不足 20 只时按实际数量展示。</p></div>
      <span className="badge" role="status">{packet?.error ? '接口请求失败' : payload ? statusText[payload.status] : '读取中'}</span>
    </header>
    <div className="research-bar">
      <label>榜单时点<select value={slot} onChange={e=>{setSlot(e.target.value as Slot);setFeedback('')}}>
        <option value="latest">最新</option><option value="14:45">14:45 盘中</option><option value="15:15">15:15 收盘</option>
      </select></label>
      <div className="tabs" role="group" aria-label="榜单排序">
        {sortLabels.map(([key,label])=><button type="button" key={key} className={sort===key?'active':''}
          aria-pressed={sort===key} onClick={()=>setSort(key)}>{label}</button>)}
      </div>
    </div>
    {(packet?.error || payload?.error) && <p className="research-foot" role="status">{packet?.error || payload?.error}</p>}
    {result && <div className="screen-summary">
      <strong>{result.day} · {result.slot} {result.slot==='14:45'?'盘中快照':'收盘确认'}</strong>
      {(payload?.stale || packet?.is_stale) && <span className="badge amber">历史快照</span>}
      <span>来源 {result.source} · {timeText(result.as_of)}</span>
      <span>扫描 {result.scanned} 只 · 合格 {result.qualified} 只 · 展示 {rows.length} 只</span>
      <span>关键字段缺失或时标无效 {(result.diagnostics.missing_required_fields??0)+(result.diagnostics.stale_or_missing_quote??0)}</span>
    </div>}
    {rows.length>0 ? <div className="table-wrap ground-volume-table"><table className="research-table" aria-label="地量严选榜单">
      <thead><tr><th>综合排名 / 标的</th><th>板块</th><th>价格 / 涨跌</th><th>地量 / 换手</th>
        <th>得分 / 回测</th><th>最大回撤</th><th>启动观察</th><th>规则价位 / 仓位</th><th>操作</th></tr></thead>
      <tbody>{rows.map(row=><tr key={row.code}>
        <td><span className="rank">TOP {String(row.rank).padStart(2,'0')}</span>
          <button type="button" className="plain row-link" onClick={()=>openChart(row.code,'line')}>{row.name}</button>
          <small className="micro">{row.code.toUpperCase()}</small></td>
        <td>{row.sector}</td>
        <td>{fmt(row.price)} <b className={tone(row.change_pct)}>{percent(row.change_pct)}</b>
          <small className="micro">行情 {timeText(row.quote_at)}</small></td>
        <td>120 日分位 {row.volume_percentile==null?'—':fmt(row.volume_percentile,1)+'%'}
          <small className="micro">量 / 60 日峰 {ratioText(row.volume_vs_60d_peak)} · 5/60 均量 {ratioText(row.volume_5_60_ratio)}</small>
          <small className="micro">换手 {fmt(row.turnover,2)}%</small></td>
        <td><strong>{fmt(row.score,2)} 分</strong>
          <small className="micro">T+5 胜率 {fmt(row.win_rate,1)}% · 平均 {percent(row.mean_return)}</small>
          <small className="micro">盈亏比 {fmt(row.payoff_ratio,2)} · 触发 {row.history.trigger_count} 次</small>
          <details><summary>各周期回测</summary>
          <div className="micro">历史字段覆盖 {row.history.covered_days}/{row.history.window_days} 日</div>
          {(['1','3','5'] as const).map(h=>{
            const m=row.history.horizons[h];
            return <div className="micro" key={h}>T+{h}{h==='1'?'（估值）':''} · {m.samples} 笔 · 胜率 {m.win_rate==null?'—':fmt(m.win_rate,1)+'%'} · 均值 {percent(m.mean_return)} · 盈亏比 {fmt(m.payoff_ratio,2)}</div>;
          })}</details></td>
        <td>{fmt(row.max_drawdown,2)}%</td>
        <td>{row.signals.join(' · ')}</td>
        <td>观察区间 {fmt(row.entry[0])}–{fmt(row.entry[1])}
          <small className="micro">S1 {fmt(row.s1)} · S2 {fmt(row.s2)} · 止损 {fmt(row.stop)}</small>
          <small className="micro">支撑 {fmt(row.support)} · 单股上限 {fmt(row.max_position_pct,0)}%</small></td>
        <td><div className="button-row">
          <button type="button" className="btn" onClick={()=>openChart(row.code,'line')}>分时</button>
          <button type="button" className="btn" onClick={()=>openChart(row.code,'kline')}>日 K</button>
          <button type="button" className="btn" onClick={()=>follow(row)}>加自选与追踪</button>
        </div></td>
      </tr>)}</tbody></table></div> : <div className="empty research-empty">
      {result?'该批次没有满足规则且具有成熟 T+5 样本的标的。':payload?statusText[payload.status]:'正在读取榜单…'}
    </div>}
    {feedback&&<p className="research-foot" role="status">{feedback}</p>}
    <div className="research-foot">排序仅重排综合分前 20；最小回撤按回撤从小到大。回测使用约 {result?.backtest_sessions??485} 个交易日的收盘信号，次日开盘作模型入场，T+1 仅估值。收益为未扣成本的历史模拟结果。价位与仓位由规则计算，页面不执行委托。</div>
  </section>;
}
