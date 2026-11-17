import {useState} from 'react';
import {csv,fmt,timeText,tone,useFeed} from './data';

type Event={id:string;messages:string[]};
type Profile='normal'|'defensive'|'paused';
type Trade={id:string;code:string;name:string;grade:'validated'|'trial';profile:Profile;shares:number;cost:number;
  entry_at:string;entry_price:number;entry_fee:number;signal_at:string;sample_wins:number;sample_size:number;
  sample_rate:number;entry_floor:number;entry_cap:number;entry_reason:string;mark:number;mark_at:string;
  exit_at:string|null;exit_price:number|null;exit_fee:number|null;exit_reason:string|null;net:number|null;net_rate:number|null;
  gross_rate?:number;finding?:string;execution?:string;best_move?:number;worst_move?:number};
type Review={day:string;equity:number;change:number;drawdown:number;partial:boolean;opened:number;closed:number;wins:number;
  exposure?:number;costs?:number;halted?:boolean;realized:number;weekly:number;weekly_gap:number;profile:Profile;reason:string;rolling_win_rate:number|null;events:Event[]};
type Account={initial_cash:number;cash:number;equity:number;weekly_opened:number;weekly_closed:number;week_target:number;
  wins:number;closed:number;exposure:number;realized:number;unrealized:number;profit_factor:number|null;sellable:number;day_halted:boolean;strategy:string;drawdown:number;max_drawdown:number;profile:Profile;as_of:string;trades:Trade[];reviews:Review[];events:Event[];
  limits:{total:number;validated:number;trial:number;positions:number;new_per_day:number}};
const names={normal:'常规',defensive:'防守',paused:'暂停新仓'};
const gradeName={validated:'通过超短线筛选',trial:'小额试验'};
const percent=(value:number|null)=>value==null?'—':fmt(value*100,2)+'%';

export function PaperTrading({revision,openChart}:{revision:number;openChart:(code:string)=>void}){
  const packet=useFeed<Account>('/api/paper/account','live',15000,revision);
  const a=packet?.data;
  const [filter,setFilter]=useState<'all'|'held'|'closed'>('all');
  const trades=a?.trades.filter(t=>filter==='all'||(filter==='held'?!t.exit_at:Boolean(t.exit_at)))??[];
  const tradeCSV=()=>a&&csv('pulse-paper-trades.csv',[
    ['标的','名称','等级','信号','开仓时间','模拟买价','股数','买入费','买入原因','历史胜出','历史样本','平仓时间','模拟卖价','卖出费','卖出原因','净盈亏','净收益率','原始价差收益率','最大有利变动','最大不利变动','执行模型','复盘结论'],
    ...a.trades.map(t=>[t.code,t.name,gradeName[t.grade],t.signal_at,t.entry_at,t.entry_price,t.shares,t.entry_fee,t.entry_reason,t.sample_wins,t.sample_size,t.exit_at,t.exit_price,t.exit_fee,t.exit_reason,t.net,t.net_rate,t.gross_rate,t.best_move,t.worst_move,t.execution??'历史分时',t.finding]),
  ]);
  const reviewCSV=()=>a&&csv('pulse-paper-review.csv',[
    ['交易日','收盘权益','当日收益率','回撤','部分估值','开仓','平仓','胜出','已实现','本周开仓','距目标','次日配置','调整原因','收盘仓位','当日费用','日内熔断','扫描和执行日志'],
    ...a.reviews.map(r=>[r.day,r.equity,r.change,r.drawdown,r.partial?'是':'否',r.opened,r.closed,r.wins,r.realized,r.weekly,r.weekly_gap,names[r.profile],r.reason,r.exposure,r.costs,r.halted?'是':'否',
      r.events.flatMap(e=>e.messages).join(' / ')]),
  ]);
  return <section id="paper" className="panel research">
    <header className="research-head"><div><div className="kicker">08 / PAPER TRADING</div><h2>模拟交易</h2>
      <p>初始资金 100 万元，服务端用实际行情生成模拟成交和收盘复盘。账户独立于页面的演示行情。</p></div><span className="badge">人民币 · 模拟账户</span></header>
    {packet?.error&&<div className="research-foot">{packet.error}</div>}
    {!a?<div className="empty">正在读取模拟账户…</div>:<>
      <div className="paper-metrics">
        <article><small>模拟账户权益</small><strong>¥{fmt(a.equity)}</strong><span>现金 ¥{fmt(a.cash)}</span></article>
        <article><small>持仓与浮动盈亏</small><strong>{percent(a.exposure)}</strong><span>浮动 ¥{fmt(a.unrealized)} · 可卖 {a.sellable} 只</span></article>
        <article><small>平仓表现</small><strong>{a.closed?percent(a.wins/a.closed):'待积累'}</strong><span>胜出 {a.wins}/{a.closed} · 已实现 ¥{fmt(a.realized)}</span></article>
        <article><small>风险与周进度</small><strong>{percent(a.drawdown)} <em>回撤</em></strong><span>最大 {percent(a.max_drawdown)} · 开仓 {a.weekly_opened}/{a.week_target}</span></article>
      </div>
      <div className="research-bar">
        <span className="badge">{a.day_halted?'日内暂停开仓':names[a.profile]}</span><span className="micro">{a.strategy} · 盈亏比 {fmt(a.profit_factor)}</span>
        <span className="micro">总仓上限 {percent(a.limits.total)} · 单只通过筛选 {percent(a.limits.validated)} · 单只小额试验 {percent(a.limits.trial)} · 日开仓至多 {a.limits.new_per_day}</span>
        <button className="btn" disabled={!a.trades.length} onClick={tradeCSV}>导出逐笔报告</button>
        <button className="btn" disabled={!a.reviews.length} onClick={reviewCSV}>导出每日复盘</button>
      </div>
      <div className="next-watch-section-head"><div><h3>逐笔交易报告</h3><p>展开查看信号依据、成交费用、退出原因和净收益。</p></div>
        <div className="tabs" aria-label="交易筛选">{([['all','全部'],['held','持仓'],['closed','已平仓']] as const).map(([key,label])=>
          <button key={key} className={filter===key?'active':''} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</button>)}</div></div>
      {!trades.length?<div className="empty">{a.trades.length?'这个筛选条件下暂无交易。':'暂无成交。入场信号、报价和风控同时通过后才会模拟开仓。'}</div>:
        <div className="paper-records">{[...trades].reverse().slice(0,50).map(t=><details key={t.id} className="paper-record">
          <summary><strong>{t.name} {t.code.slice(2)}</strong> · {gradeName[t.grade]} · {timeText(t.entry_at)} · {t.shares} 股 ·
            {' '}<b className={tone(t.net)}>{t.exit_at?'净盈亏 ¥'+fmt(t.net):'持仓中 · 估值 '+fmt(t.mark)}</b></summary>
          <p>{t.entry_reason} 触发 {timeText(t.signal_at)}，允许区间 {fmt(t.entry_floor)}–{fmt(t.entry_cap)}，历史样本 {t.sample_wins}/{t.sample_size}（{percent(t.sample_rate)}）。</p>
          <p>模拟买入 {fmt(t.entry_price,4)} 元，买入费用 {fmt(t.entry_fee)} 元，成本 {fmt(t.cost)} 元。{t.exit_at
            ?' 平仓 '+timeText(t.exit_at)+'，模拟卖出 '+fmt(t.exit_price,4)+' 元，卖出费用 '+fmt(t.exit_fee)+' 元，净收益 '+percent(t.net_rate)+'。'+t.exit_reason
            :' 最近估值 '+timeText(t.mark_at)+'。'}</p>
          {t.execution&&<p>盘口执行 · 最佳估值变动 {percent(t.best_move??null)} · 最差估值变动 {percent(t.worst_move??null)}</p>}
          {t.finding&&<p>复盘：原始价差 {percent(t.gross_rate??null)}。{t.finding}</p>}
          <button className="btn" onClick={()=>openChart(t.code)}>查看走势</button>
        </details>)}</div>}
      <div className="next-watch-section-head"><div><h3>每日复盘与策略修正</h3><p>15:00 核对持仓估值，并按当日收盘回撤和近十笔表现调整下一交易日的风险配置。</p></div></div>
      {!a.reviews.length?<div className="empty">暂无收盘复盘记录。</div>:
        <div className="paper-records">{[...a.reviews].reverse().slice(0,30).map((r,i)=><details key={r.day} className="paper-record" open={i===0}>
          <summary><strong>{r.day}</strong> · 权益 ¥{fmt(r.equity)} · 当日 {percent(r.change)} · 回撤 {percent(r.drawdown)}{r.partial?' · 部分估值':''}</summary>
          <p>开仓 {r.opened}，平仓 {r.closed}，胜出 {r.wins}，已实现 ¥{fmt(r.realized)}。本周累计 {r.weekly} 笔，距目标 {r.weekly_gap} 笔。{r.costs!=null&&' 费用 ¥'+fmt(r.costs)+'，收盘仓位 '+percent(r.exposure??null)+'。'}</p>
          <p>下一交易日配置：{names[r.profile]}。{r.reason} 近十笔（或现有已平仓）胜率 {percent(r.rolling_win_rate)}。</p>
          {!!r.events.length&&<ul>{r.events.map(e=><li key={e.id}>{e.id.slice(11,16)} · {e.messages.join(' ')}</li>)}</ul>}
        </details>)}</div>}
      <details className="research-foot hourly-explain"><summary>策略、风险阈值和周交易目标</summary>
        <p>每轮按真实 30 分钟行情复核入场区间，优先采用通过超短线筛选的信号。周开仓不足五笔时，可用更小仓位验证样本不少于 5 笔、历史胜率至少 50%、已知平均净收益为正且覆盖达标的次级信号。数据或风控未通过时记录原因。</p>
        <p>开仓取分时确认后的卖一，平仓取可卖日买一。触及 −2% 或 +3% 可提前退出，次交易日 10:00 起核对剩余仓位。佣金按 0.05%（最低 ¥5），过户费按 0.001%，卖出另计 0.05% 印花税，滑点 0.10%。日内权益损失超过 1% 停止新仓。收盘回撤达 1.5% 或近十笔胜率低于 55% 降档，收盘回撤达 3% 暂停新开仓，继续处理已有持仓。策略样本按历史分时模型计算，账户指标按实际模拟盘口执行记录计算。</p>
      </details>
      <div className="research-foot">账户读取时间 {timeText(a.as_of)}。盘中按最近一次核对价格估值，交易和复盘记录保存在本地服务。</div>
    </>}
  </section>;
}
