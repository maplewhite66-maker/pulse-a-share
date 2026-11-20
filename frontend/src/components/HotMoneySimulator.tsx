import {useState} from 'react';
import {fmt,percent,timeText,useFeed,useSaved,type Mode} from '../data';
import {initialPaper,paperFill,type Account,type Quote} from '../style-paper';

type Metric={key:string;label:string;trades:number;signals:number;unresolved:number;win_rate:number|null;annualized:number|null;
  mean_return:number|null;payoff:number|null;max_drawdown:number|null;next_day_premium:number|null;exit_horizon:number};
type Signal=Quote&{styles:string[];price:number};
type Payload={styles:Metric[];quotes:Signal[];market_open:boolean;day:string;history_note:string|null;live_note:string|null;
  backtest:{sessions:number;from_day:string;to_day:string;universe:number;source:string;results:Metric[]}|null};
const pct=(value:number|null)=>value==null?'样本不足':percent(value);

export function HotMoneySimulator({mode,revision,openChart}:{mode:Mode;revision:number;openChart:(code:string)=>void}){
  const [style,setStyle]=useState('cycle');
  const [message,setMessage]=useState('');
  const [accounts,setAccounts]=useSaved<Record<string,Account>>('pulse-v4-style-paper-v1',{});
  const account=accounts[style]??initialPaper;
  const requested=account.positions.map(p=>p.code).sort().slice(0,20).join(',');
  const feed=useFeed<Payload>('/api/strategy/hot-money-sim'+(requested?'?codes='+encodeURIComponent(requested):''),mode,5000,revision);
  const data=feed?.data, replay=data?.backtest;
  const byCode=new Map(data?.quotes.map(row=>[row.code,row]));
  const signals=data?.quotes.filter(row=>row.styles.includes(style))??[];
  function useQuote(quote:Signal,side:'buy'|'sell'){
    if(!data)return;
    if(side==='buy'&&!quote.styles.includes(style)){setMessage('该标的已离开当前风格信号');return}
    const fill=paperFill(account,quote,side,data.day,data.market_open,Date.now());
    if(fill.error){setMessage(fill.error);return}
    if(fill.account)setAccounts(prev=>({...prev,[style]:fill.account!}));
    setMessage(`${quote.name} · 模拟${side==='buy'?'买入':'卖出'}已登记`);
  }
  return <section id="hot-money" className="panel research">
    <header className="research-head"><div><h2>风格回测与模拟交易室</h2>
      <p>五套公开条件的风格模型。名称标记的是研究规则，与个人实际交易无关。</p></div><span className="badge">本机模拟</span></header>
    {data?<><div className="research-foot">{replay?`${replay.from_day} 至 ${replay.to_day} · ${replay.sessions} 个交易日 · ${replay.universe} 只 · ${replay.source||'授权档案'}`:
      data.history_note??'等待历史数据'}</div>
    <div className="table-wrap"><table className="research-table"><thead><tr><th>风格</th><th>信号 / 成交</th><th>胜率 ↓</th><th>年化</th><th>单笔均值</th><th>盈亏比</th><th>最大回撤</th><th>次日开盘溢价</th></tr></thead>
      <tbody>{(replay?.results??data.styles).map(r=><tr key={r.key}><td><button className="plain" onClick={()=>{setStyle(r.key);setMessage('')}}>{r.label}</button></td>
        <td>{replay?`${r.signals} / ${r.trades}`:'待回测'}{r.unresolved>0&&<small className="micro">未退出 {r.unresolved} 笔</small>}</td>
        <td>{pct(r.win_rate)}</td><td>{pct(r.annualized)}</td><td>{pct(r.mean_return)}</td>
        <td>{r.payoff==null?'样本不足':fmt(r.payoff,2)}</td><td>{pct(r.max_drawdown)}</td><td>{pct(r.next_day_premium)}</td>
      </tr>)}</tbody></table></div>
    <div className="research-bar"><label>模拟风格<select value={style} onChange={e=>{setStyle(e.target.value);setMessage('')}}>
      {data.styles.map(r=><option key={r.key} value={r.key}>{r.label}</option>)}</select></label>
      <span>现金 ¥{fmt(account.cash)} · 已实现 ¥{fmt(account.realized)}</span>
      <span role="status">{message}</span>
    </div>
    {signals.length?<div className="table-wrap"><table className="research-table"><thead><tr><th>当前入场信号</th><th>行情</th><th>模拟操作</th></tr></thead>
      <tbody>{signals.map(r=><tr key={r.code}><td><button className="plain" onClick={()=>openChart(r.code)}>{r.name}</button></td>
        <td>{fmt(r.price)} · {timeText(r.market_time)}</td><td><button className="btn" disabled={!r.entry_allowed||r.ask==null} onClick={()=>useQuote(r,'buy')}>模拟买入</button></td></tr>)}</tbody>
    </table></div>:<p className="research-foot">{data.live_note??'当前未出现该风格的入场信号。'}</p>}
    {account.positions.length>0&&<div className="table-wrap"><table className="research-table"><thead><tr><th>模拟持仓</th><th>成本 / 股数</th><th>退出提示</th><th>操作</th></tr></thead>
      <tbody>{account.positions.map(p=>{
        const quote=byCode.get(p.code);
        const canSell=Boolean(data.market_open&&quote?.bid&&quote.sell_shares>=p.shares&&data.day>p.day);
        const movement=quote?.bid?quote.bid/p.entry-1:null;
        return <tr key={p.code}><td><button className="plain" onClick={()=>openChart(p.code)}>{p.name}</button></td>
          <td>{fmt(p.entry)} / {p.shares}</td><td>{p.day>=data.day?'T+1 锁定':movement==null?'等待有效买一':
            movement>=.03?'达到 +3% 模拟止盈观察线':movement<=-.025?'达到 −2.5% 模拟防守线':'可模拟退出'}</td>
          <td><button className="btn" disabled={!canSell} onClick={()=>{if(quote)useQuote(quote,'sell')}}>模拟卖出</button></td></tr>
      })}</tbody></table></div>}
    </>:<p className="empty">{feed?.error??'正在读取风格档案…'}</p>}
    <p className="research-foot">回放按 T+1 开盘入场，按风格在 T+2 至 T+5 的计划时点退出，计入模拟费用与滑点。演练账本只保存在当前浏览器。</p>
  </section>;
}
