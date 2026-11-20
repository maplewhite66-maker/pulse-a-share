import {useState,type FormEvent} from 'react';
import {csv,fmt,timeText,ticker,useSaved,type Mode,type Quote} from './data';
import {emptyPlan,estimateBoardLots,fillError,summarize,tradePlanStorage,type Fill,type Side,type Stage,type TradePlan} from './trade-ledger';
import {DailyTradeReview} from './trade-review';

const label={buy:'买入',sell:'卖出'};
const initial={price:'',quantity:'',fee:'0',note:''};
const todaySH=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short'}).format(new Date());

export function TradePlans({mode,code,quote,watch,choose,openChart,draftPrice}:{
  mode:Mode;code:string;quote?:Quote;watch:string[];choose:(code:string)=>void;openChart:(code:string)=>void;draftPrice?:number;
}){
  const [plans,setPlans]=useSaved<Record<string,TradePlan>>(tradePlanStorage,{});
  const [input,setInput]=useState(''),[error,setError]=useState('');
  const key=mode+':'+code;
  const plan=plans[key]??emptyPlan;
  const names=Array.from(new Set([code,...watch,...Object.keys(plans).filter(k=>k.startsWith(mode+':')).map(k=>k.split(':')[1])]));
  const edit=(action:(prev:TradePlan)=>TradePlan)=>setPlans(old=>({...old,[key]:action(old[key]??emptyPlan)}));
  return <section className="panel research" id="plan">
    <header className="research-head"><div><h2>交易复盘与计划</h2>
      <p>按交易日查看记录，按标的登记成交和分批计划。</p></div><span className="badge">{mode==='demo'?'演示账本':'真实账本'}</span></header>
    {mode==='live'&&<DailyTradeReview plans={plans} setPlans={setPlans} choose={choose}/>}
    <div className="research-bar"><label>计划标的<select value={code} onChange={e=>choose(e.target.value)}>
      {names.map(symbol=><option key={symbol} value={symbol}>{symbol.toUpperCase()}{symbol===code&&quote?' · '+quote.name:''}</option>)}</select></label>
      <form className="plan-switch" onSubmit={e=>{e.preventDefault();const s=ticker(input);
        if(!s||/^(sh000|sz399)/.test(s)){setError('请输入六位股票代码');return}
        choose(s);setInput('');setError('')}}>
        <label>切换股票<input value={input} onChange={e=>setInput(e.target.value)} placeholder="例如 600519" /></label>
        <button className="btn">打开计划</button></form>
      <button className="btn" onClick={()=>openChart(code)}>查看走势</button>
      {error&&<span role="status">{error}</span>}
    </div>
    <PlanDetail key={key+':'+(draftPrice??'')} code={code} mode={mode} quote={quote} plan={plan} edit={edit} draftPrice={draftPrice}/>
  </section>;
}

function PlanDetail({code,mode,quote,plan,edit,draftPrice}:{
  code:string;mode:Mode;quote?:Quote;plan:TradePlan;edit:(fn:(previous:TradePlan)=>TradePlan)=>void;draftPrice?:number;
}){
  const [tab,setTab]=useState<'plan'|'fill'>('plan');
  const [side,setSide]=useState<Side>('buy'),[day,setDay]=useState(todaySH);
  const [form,setForm]=useState({...initial,price:draftPrice==null?'':String(draftPrice)});
  const [feedback,setFeedback]=useState('');
  const [budget,setBudget]=useState(plan.budget?String(plan.budget):'');
  const [allocation,setAllocation]=useState('');
  const position=summarize(plan.fills);
  const planned=plan.stages.reduce((total,row)=>total+(row.side==='buy'?row.price*row.quantity:0),0);
  const cashLeft=plan.budget?plan.budget-position.basis-planned:null;
  const price=quote?.is_stale?undefined:quote?.price;
  const input=(key:keyof typeof form,value:string)=>setForm(prev=>({...prev,[key]:value}));
  const useLotEstimate=side==='buy'&&/^(sh60|sz00)\d{4}$/.test(code);
  const estimate=useLotEstimate?estimateBoardLots(budget,form.price,allocation):null;
  function submit(event:FormEvent<HTMLFormElement>){
    event.preventDefault();
    const entry={side,price:Number(form.price),quantity:Number(form.quantity)};
    if(tab==='fill'){
      const fill:Fill={...entry,fee:Number(form.fee),day};
      const invalid=fillError(fill,plan,todaySH());
      if(invalid){setFeedback(invalid);return}
      edit(old=>fillError(fill,old,todaySH())?old:{...old,fills:[...old.fills,fill]});
      setFeedback('已登记成交。对应计划档可手动移除。');
    }else{
      const stage:Stage={...entry,note:form.note.trim()};
      const gross=stage.price*stage.quantity;
      if(!Number.isFinite(gross)||stage.price<=0||!Number.isSafeInteger(stage.quantity)||stage.quantity<=0){
        setFeedback('填写有效条件价和整数股数。');return
      }
      if(stage.side==='buy'&&cashLeft!==null&&gross>cashLeft+.005){
        setFeedback('本档买入金额超过剩余计划预算。');return
      }
      edit(old=>({...old,stages:[...old.stages,stage]}));setFeedback('已加入分批计划。');
    }
    setForm(initial);
  }
  const exportPlan=()=>csv('pulse-'+mode+'-'+code+'-plan.csv',[
    ['数据模式','标的','记录','方向','价格','股数','费用','日期','备注'],
    ...plan.stages.map(row=>[mode,code,'计划',label[row.side],row.price,row.quantity,null,null,row.note]),
    ...plan.fills.map(row=>[mode,code,row.reportedName?'持仓成本':'成交',label[row.side],row.price,row.quantity,row.reportedName?null:row.fee,row.day,row.reportedName??null]),
  ]);
  return <>
    <div className="screen-summary plan-summary">
      <strong>{quote?.name??plan.fills.find(fill=>fill.reportedName)?.reportedName??code.toUpperCase()}</strong>
      <span>现价 {fmt(price)}{quote?.market_time?' · '+timeText(quote.market_time):''}</span>
      <span>持仓 {fmt(position.shares,0)} 股</span><span>成本均价 {fmt(position.average,3)}</span>
      <span>累计费用 ¥{fmt(position.fees)}</span><span>已实现盈亏 ¥{fmt(position.realized)}</span>
      <span>浮动盈亏 ¥{fmt(price==null||!position.shares?null:price*position.shares-position.basis)}</span>
      <button className="btn" disabled={!plan.stages.length&&!plan.fills.length} onClick={exportPlan}>导出计划及台账</button>
    </div>
    <div className="research-bar plan-controls">
      <div className="tabs" role="group" aria-label="计划或成交">
        <button className={tab==='plan'?'active':''} aria-pressed={tab==='plan'} onClick={()=>{setTab('plan');setFeedback('')}}>分批计划</button>
        <button className={tab==='fill'?'active':''} aria-pressed={tab==='fill'} onClick={()=>{setTab('fill');setFeedback('')}}>成交台账</button>
      </div>
      <form className="plan-switch" onSubmit={event=>{event.preventDefault();const value=Number(budget);
        if(Number.isFinite(value)&&value>=0){edit(old=>({...old,budget:value}));setFeedback('已保存预算。')}}}>
        <label>预算（元，0 为不限）<input type="number" min="0" step=".01" value={budget} onChange={e=>setBudget(e.target.value)} /></label>
        <button className="btn">保存预算</button>
      </form>
      <span className="micro">未成交买入计划 ¥{fmt(planned)} · 预算余量 {cashLeft===null?'不限':'¥'+fmt(cashLeft)}</span>
    </div>
    <form className="research-bar plan-controls" onSubmit={submit}>
      <label>方向<select value={side} onChange={e=>setSide(e.target.value as Side)}><option value="buy">买入</option><option value="sell">卖出</option></select></label>
      <label>{tab==='plan'?'条件价':'成交价'}<input required type="number" min=".01" step=".01" value={form.price} onChange={e=>input('price',e.target.value)}/></label>
      <label>股数<input required type="number" min="1" step="1" value={form.quantity} onChange={e=>input('quantity',e.target.value)}/></label>
      {tab==='fill'?<><label>费用（元）<input required type="number" min="0" step=".01" value={form.fee} onChange={e=>input('fee',e.target.value)}/></label>
        <label>成交日期<input required type="date" value={day} onChange={e=>setDay(e.target.value)}/></label></>:
        <label>备注<input value={form.note} maxLength={100} onChange={e=>input('note',e.target.value)} placeholder="例如缩量回踩"/></label>}
      <button className="btn">{tab==='plan'?'添加计划档':'登记成交'}</button>
    </form>
    {useLotEstimate&&<div className="research-bar plan-controls">
      <label>预算占比（%）<input type="number" min=".01" max="100" step=".01" value={allocation}
        onChange={e=>setAllocation(e.target.value)}/></label>
      <span className="micro">{estimate
        ? `估算 ${fmt(estimate.shares,0)} 股（${fmt(estimate.lots,0)} 手），金额 ¥${fmt(estimate.gross)}，未含费用。`
        : '输入预算、价格及占比，按每手 100 股向下取整。'}</span>
      <button type="button" className="btn" disabled={!estimate?.shares}
        onClick={()=>{if(estimate?.shares)input('quantity',String(estimate.shares))}}>填入股数</button>
    </div>}
    {feedback&&<div className="research-foot screen-feedback" role="status">{feedback}</div>}
    <div className="table-wrap"><table className="research-table"><thead><tr><th>序号</th><th>方向</th><th>价格</th><th>股数</th><th>金额（元）</th>
      {tab==='plan'?<><th>条件</th><th>备注</th></>:<><th>费用</th><th>成交日</th></>}<th>操作</th></tr></thead>
      <tbody>{tab==='plan'?plan.stages.map((row,i)=><tr key={i}><td>{i+1}</td><td>{label[row.side]}</td><td>{fmt(row.price)}</td>
        <td>{row.quantity}</td><td>{fmt(row.price*row.quantity)}</td><td>{price==null?'等待有效报价':(row.side==='buy'?price<=row.price:price>=row.price)?'已触价':'待触价'}</td>
        <td>{row.note||'—'}</td><td><button className="btn" onClick={()=>edit(old=>({...old,stages:old.stages.filter((_,j)=>i!==j)}))}>移除</button></td></tr>):
        plan.fills.map((row,i)=><tr key={i}><td>{i+1}</td><td>{label[row.side]}</td><td>{fmt(row.price,row.reportedName?3:2)}{row.reportedName?' · 持仓成本':''}</td><td>{row.quantity}</td>
          <td>{fmt(row.price*row.quantity)}</td><td>{row.reportedName?'—':fmt(row.fee)}</td><td>{row.day}</td>
          <td>{i===plan.fills.length-1&&<button className="btn" onClick={()=>edit(old=>({...old,fills:old.fills.slice(0,-1)}))}>撤销末笔</button>}</td></tr>)}</tbody></table></div>
    {!(tab==='plan'?plan.stages.length:plan.fills.length)&&<div className="empty">{tab==='plan'?'暂无分批计划':'暂无成交记录'}</div>}
    <div className="research-foot">计划保存在当前浏览器，真实与演示分开。成本按成交顺序移动加权，预算余量为预算减持仓成本及未成交买入计划本金。触价用于观察，成交需手工登记。</div>
  </>;
}
