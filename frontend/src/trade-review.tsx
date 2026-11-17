import {useEffect,useState} from 'react';
import {fmt,saved} from './data';
import {mergeTradeReview,parseTradeReview,summarize,tradePlanStorage,type TradePlan} from './trade-ledger';

function reviewFromHash(){
  return location.hash.startsWith('#plan?')?new URLSearchParams(location.hash.slice(6)).get('review'):null;
}

export function DailyTradeReview({plans,setPlans,choose}:{
  plans:Record<string,TradePlan>;setPlans:(plans:Record<string,TradePlan>)=>void;choose:(code:string)=>void;
}){
  const [importValue,setImportValue]=useState(reviewFromHash);
  const [selectedDay,setSelectedDay]=useState('');
  const [feedback,setFeedback]=useState('');
  useEffect(()=>{
    const refresh=()=>setImportValue(reviewFromHash());
    window.addEventListener('hashchange',refresh);
    return ()=>window.removeEventListener('hashchange',refresh);
  },[]);
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short'}).format(new Date());
  const entries=Object.entries(plans).filter(([key])=>key.startsWith('live:'));
  const days=[...new Set(entries.flatMap(([,plan])=>plan.fills.map(fill=>fill.day)))].sort().reverse();
  const day=selectedDay||days[0]||today;
  const review=importValue===null?null:parseTradeReview(importValue,today);
  const rows=entries.flatMap(([key,plan])=>{
    const name=plan.fills.find(fill=>fill.reportedName)?.reportedName;
    const shares=summarize(plan.fills.filter(fill=>fill.day<=day)).shares;
    return plan.fills.filter(fill=>fill.day===day).map(fill=>({code:key.slice(5),fill,name,shares}));
  });
  function storeReview(){
    if(!review)return;
    try{
      const {plans:next,added}=mergeTradeReview(saved(tradePlanStorage,plans),review);
      localStorage.setItem('pulse2:'+tradePlanStorage,JSON.stringify(next));
      setPlans(next);
      setSelectedDay(review.day);
      setImportValue(null);
      history.replaceState(history.state,'',location.pathname+location.search+'#plan');
      setFeedback(added?'已保存 '+added+' 笔至本机复盘。':'这批记录已在本机复盘。');
    }catch{setFeedback('浏览器无法保存，允许此网站存储数据后重试。');}
  }
  return <section aria-label="每日交易复盘">
    <div className="research-bar"><h3>每日交易复盘</h3>
      <label>交易日<select value={day} onChange={e=>setSelectedDay(e.target.value)}>
        {[...new Set([day,...days])].map(date=><option key={date}>{date}</option>)}
      </select></label>
    </div>
    {importValue!==null&&<div className="research-bar">
      {review?<><span>{review.day} · {review.rows.map(({fill})=>fill.reportedName+' '+fmt(fill.price,3)+' 元 × '+fill.quantity+' 股').join('；')}</span>
        <button className="btn chosen" onClick={storeReview}>保存到本机复盘</button></>:<span>复盘导入链接无效。</span>}
    </div>}
    {feedback&&<div className="research-foot" role="status">{feedback}</div>}
    {rows.length?<div className="table-wrap"><table className="research-table"><thead><tr>
      <th>标的</th><th>方向</th><th>价格 / 成本</th><th>股数</th><th>金额</th><th>当日记录后持仓</th>
    </tr></thead><tbody>{rows.map(({code,fill,name,shares},i)=><tr key={i}>
      <td><button className="btn" onClick={()=>choose(code)}>{name??code.slice(2)}{name?' '+code.slice(2):''}</button></td>
      <td>{fill.side==='buy'?'买入':'卖出'}</td><td>{fmt(fill.price,fill.reportedName?3:2)} 元{fill.reportedName?' · 持仓成本':''}</td>
      <td>{fmt(fill.quantity,0)}</td><td>¥{fmt(fill.price*fill.quantity)}</td><td>{fmt(shares,0)} 股</td>
    </tr>)}</tbody></table></div>:<div className="empty">该交易日暂无记录。</div>}
    <div className="research-foot">记录保存在当前浏览器。导入的持仓成本来自用户登记，手续费未单列。</div>
  </section>;
}
