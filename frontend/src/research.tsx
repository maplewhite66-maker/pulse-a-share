import {useEffect,useState,type Dispatch,type FormEvent,type SetStateAction} from 'react';
import {externalUrl,csv,fmt,money,percent,ticker,timeText,tone,useFeed,useSaved,type Mode,type Num,type Packet,type Quote} from './data';
import {observe,TRACK_LIMIT,type Tracked,type Sample} from './tracking';

export function ResearchSource({packet}:{packet:Packet<unknown>|null}){
  if(!packet)return null;
  return <div className={'source'+(packet.error?' bad':'')}>
    <span>{packet.source_url&&packet.source?<a href={externalUrl(packet.source_url)} target="_blank" rel="noreferrer">{packet.source} ↗</a>:packet.source||'行情源待确认'}</span>
    {packet.market_time&&<span>行情 {timeText(packet.market_time)}</span>}
    {packet.fetched_at&&<span>快照 {timeText(packet.fetched_at)}</span>}
    {packet.is_stale&&packet.data!=null&&<b>上次有效快照</b>}
    {packet.error&&<b>{packet.error}</b>}
    {packet.note&&<span>{packet.note}</span>}
  </div>;
}

function Sparkline({samples}:{samples:Sample[]}){
  if(samples.length<2)return <div className="spark-empty">再收到一条有效报价后显示价格轨迹</div>;
  const prices=samples.map(r=>r.price);
  const low=Math.min(...prices),high=Math.max(...prices);
  const first=Date.parse(samples[0].time),last=Date.parse(samples.at(-1)!.time);
  const points=samples.map(r=>{
    const x=10+280*(Date.parse(r.time)-first)/Math.max(last-first,1);
    const y=high===low?28:48-39*(r.price-low)/(high-low);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  return <svg viewBox="0 0 300 56" className="track-spark" role="img" aria-label="追踪期间的报价轨迹">
    <path d="M10 49H290 M10 29H290 M10 9H290" fill="none" stroke="#263849" strokeDasharray="3 4"/>
    <polyline points={points} fill="none" stroke="#58e0c2" strokeWidth="1.6" vectorEffect="non-scaling-stroke"/>
  </svg>;
}

type SetTracks=Dispatch<SetStateAction<Tracked[]>>;
export function Tracking({mode,items,setItems,active,packet,visible,add,openChart,focus}:{
  mode:Mode;items:Tracked[];setItems:SetTracks;active:boolean;packet:Packet<Quote[]>|null;visible:boolean;
  add:(code:string)=>string|null;openChart:(code:string)=>void;focus:{code:string;visit:number}|null;
}){
  const [selected,setSelected]=useState<string|null>(null);
  const [code,setCode]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{if(focus)setSelected(focus.code)},[focus]);
  const current=items.find(r=>r.code===selected)??items[0];
  useEffect(()=>{
    if(!packet?.data||packet.is_stale)return;
    const byCode=new Map(packet.data.map(q=>[q.code,q]));
    setItems(old=>{
      const next=old.map(r=>{const q=byCode.get(r.code);return q?observe(r,q,active):r});
      return next.some((r,i)=>r!==old[i])?next:old;
    });
  },[active,packet,setItems]);
  const submit=(e:FormEvent)=>{
    e.preventDefault();
    const normalized=ticker(code);
    if(!normalized){setError('请输入六位股票代码');return}
    const message=add(normalized);
    setError(message??'');
    if(!message){setSelected(normalized);setCode('')}
  };
  const byCode=new Map(packet?.data?.map(q=>[q.code,q]));
  return <section id="tracking" className="panel research" hidden={!visible}>
    <header className="research-head"><div><div className="kicker">02 / STOCK TRACKER</div><h2>个股追踪</h2>
      <p>记录首次有效报价作为基准。设置价格上下限，跟踪后续报价和触发时间。</p></div><span className="badge">{items.length} / {TRACK_LIMIT} 只 · {mode==='demo'?'演示':'真实'}</span></header>
    <form className="research-bar" onSubmit={submit}><label>股票代码<input aria-label="追踪股票代码" placeholder="例如 600519" value={code} onChange={e=>setCode(e.target.value)}/></label>
      <button className="btn research-primary" type="submit">添加追踪</button>{error&&<span className="research-error" role="alert">{error}</span>}
    </form>
    {items.length?<div className="tracking-grid">
      <div className="table-wrap research-table"><table><thead><tr><th>标的</th><th>最新价</th><th>当日涨跌</th><th>较基准变化</th><th>状态</th></tr></thead><tbody>
        {items.map(r=>{
          const q=byCode.get(r.code),sample=r.samples.at(-1);
          const stale=packet?.is_stale||q?.is_stale;
          const price=stale?sample?.price??q?.price:q?.price??sample?.price;
          const move=stale?sample?.pct??q?.pct:q?.pct??sample?.pct;
          const relative=price!=null&&r.baseline?(price/r.baseline.price-1)*100:null;
          const pending=stale&&price!=null?'报价滞后':!q?sample?'使用上次记录':'待报价':'';
          const state=pending||(r.upper!=null&&price!=null&&price>=r.upper?'达到上限':
            r.lower!=null&&price!=null&&price<=r.lower?'达到下限':r.upper!=null||r.lower!=null?'追踪中':'未设价格线');
          return <tr key={r.code} className={current?.code===r.code?'picked':''}>
            <td><button className="plain row-link" onClick={()=>setSelected(r.code)}>{r.name}<small>{r.code.toUpperCase()}</small></button></td>
            <td>{fmt(price)}</td><td className={tone(move)}>{percent(move)}</td>
            <td className={tone(relative)}>{percent(relative)}</td><td>{state}</td></tr>;
        })}
      </tbody></table></div>
      {current&&<TrackDetail key={mode+':'+current.code} stock={current}
        update={change=>setItems(old=>old.map(r=>r.code===current.code?change(r):r))}
        remove={()=>setItems(old=>old.filter(r=>r.code!==current.code))} openChart={()=>openChart(current.code)}/>}
    </div>:<div className="empty research-empty">输入股票代码开始追踪，也可以从走势页或选股结果添加。</div>}
    <ResearchSource packet={packet}/>
    <div className="research-foot">页面打开时更新报价。价格穿越记录保存在当前浏览器，真实与演示各自保存。</div>
  </section>;
}

function TrackDetail({stock,update,remove,openChart}:{stock:Tracked;update:(fn:(r:Tracked)=>Tracked)=>void;remove:()=>void;openChart:()=>void}){
  const [upper,setUpper]=useState(stock.upper?.toString()??''),[lower,setLower]=useState(stock.lower?.toString()??'');
  const [note,setNote]=useState(stock.note),[message,setMessage]=useState('');
  const last=stock.samples.at(-1);
  const save=(e:FormEvent)=>{
    e.preventDefault();
    const high=upper===''?null:Number(upper),low=lower===''?null:Number(lower);
    if((high!=null&&(!Number.isFinite(high)||high<=0))||(low!=null&&(!Number.isFinite(low)||low<=0))||(high!=null&&low!=null&&high<=low)){
      setMessage('价格必须大于 0，且上限须高于下限。');return;
    }
    update(r=>({...r,upper:high,lower:low,note:note.trim()}));setMessage('追踪条件已保存');
  };
  return <article className="track-detail"><div className="track-title"><div><h3>{stock.name}</h3><span className="muted">{stock.code.toUpperCase()}</span></div>
    <button className="btn" onClick={openChart}>查看走势</button></div>
    <div className="track-baseline"><span>基准 <b>{fmt(stock.baseline?.price)}</b></span><span>最近记录 <b>{fmt(last?.price)}</b></span></div>
    <div className="micro">{stock.baseline?'基准报价 '+timeText(stock.baseline.time):'等待读取基准报价'}</div>
    <Sparkline samples={stock.samples}/>
    {last&&<div className="micro">最近报价 {timeText(last.time)} · {stock.samples.length} 条本地快照</div>}
    <form className="track-form" onSubmit={save}>
      <label>下限价（元）<input type="number" min=".001" step="any" value={lower} onChange={e=>setLower(e.target.value)}/></label>
      <label>上限价（元）<input type="number" min=".001" step="any" value={upper} onChange={e=>setUpper(e.target.value)}/></label>
      <label className="track-note">追踪备注<textarea maxLength={240} value={note} placeholder="记录观察理由或计划" onChange={e=>setNote(e.target.value)}/></label>
      <div className="track-note button-row"><button className="btn research-primary" type="submit">保存条件</button>
        <button className="btn" type="button" disabled={!last} onClick={()=>{update(r=>({...r,baseline:r.samples.at(-1)??r.baseline}));setMessage('基准已更新为最近有效报价')}}>重置基准</button>
        <button className="btn track-remove" type="button" onClick={remove}>移除追踪</button></div>
    </form>
    {message&&<div className="micro track-message" role="status">{message}</div>}
    <div className="track-events"><h4>价格穿越记录 <span className="badge">{stock.events.length}</span></h4>
      {stock.events.length?stock.events.slice(-6).reverse().map(event=><div key={event.time+event.direction}>
        <span>{event.direction==='upper'?'向上穿越':'向下穿越'} {fmt(event.target)} 元</span><b>{fmt(event.price)} 元</b><small>{timeText(event.time)}</small>
      </div>):<p className="micro">暂无穿越记录。已有两条有效报价并跨过价格线时记录。</p>}
    </div>
  </article>;
}

const metrics=[
  ['pct','涨跌幅','%',1],['price','最新价','元',1],['amount','成交额','亿',1e8],
  ['turnover','换手率','%',1],['vol_ratio','量比','倍',1],['float_cap','流通市值','亿',1e8],['main_net','主力净额','亿',1e8]
] as const;
type MetricKey=typeof metrics[number][0];
type Scope='market'|'watch'|'board';
type Bounds={minimum:string;maximum:string};
type RangeDraft=Partial<Record<MetricKey,Bounds>>;
type ScreenRow={code:string;name:string;market_time:string|null}&Record<MetricKey,Num>;
type Result={items:ScreenRow[];universe:number;scanned:number;eligible:number;missing:number;matched:number;scope:Scope};
type Board={code:string;name:string;visit:number};
const presets:{label:string;sort:MetricKey;ranges:RangeDraft}[]=[
  {label:'放量上涨',sort:'vol_ratio',ranges:{pct:{minimum:'2',maximum:''},vol_ratio:{minimum:'1.5',maximum:''}}},
  {label:'资金净流入',sort:'main_net',ranges:{main_net:{minimum:'0.3',maximum:''},amount:{minimum:'3',maximum:''}}},
  {label:'高流动性',sort:'amount',ranges:{amount:{minimum:'10',maximum:''},float_cap:{minimum:'50',maximum:''}}},
];
export function Screener({mode,watch,tracked,board,visible,revision,openChart,addWatch,addTrack}:{
  mode:Mode;watch:string[];tracked:Tracked[];board:Board|null;visible:boolean;revision:number;openChart:(code:string)=>void;
  addWatch:(code:string)=>string|null;addTrack:(code:string)=>string|null;
}){
  const [ranges,setRanges]=useSaved<RangeDraft>('screenRanges',{});
  const [scope,setScope]=useState<Scope>('market');
  const [exchange,setExchange]=useState('all'),[excludeRisk,setExcludeRisk]=useState(true);
  const [sort,setSort]=useState<MetricKey>('pct'),[direction,setDirection]=useState('desc'),[limit,setLimit]=useState(50);
  const [body,setBody]=useState<string>();
  const [run,setRun]=useState(0);
  const [formError,setFormError]=useState(''),[feedback,setFeedback]=useState('');
  useEffect(()=>{
    setScope(previous=>board?'board':previous==='board'?'market':previous);
    setBody(undefined);setFeedback('');setFormError('');
  },[board]);
  const packet=useFeed<Result>(visible&&body?'/api/stocks/screen':null,mode,15_000,run+revision,body);
  const result=packet?.data;
  const submit=(e:FormEvent)=>{
    e.preventDefault();
    setFormError('');setFeedback('');
    const active=[];
    for(const [field,label,,multiplier] of metrics){
      const bound=ranges[field];
      if(!bound)continue;
      const min=bound.minimum===''?null:Number(bound.minimum)*multiplier;
      const max=bound.maximum===''?null:Number(bound.maximum)*multiplier;
      if(min===null&&max===null)continue;
      if((min!==null&&!Number.isFinite(min))||(max!==null&&!Number.isFinite(max))||(min!==null&&max!==null&&min>max)){
        setFormError(label+'的下限不能高于上限');return;
      }
      active.push({field,minimum:min,maximum:max});
    }
    setBody(JSON.stringify({scope,exchange,board:scope==='board'?board?.code:null,codes:scope==='watch'?watch:[],
      exclude_risk:excludeRisk,ranges:active,sort_by:sort,direction,limit}));
    setRun(n=>n+1);
  };
  function changeRange(field:MetricKey,key:keyof Bounds,value:string){
    setRanges(old=>({...old,[field]:{...(old[field]??{minimum:'',maximum:''}),[key]:value}}));
  }
  const exportResults=()=>csv('Pulse-screener-'+mode+'.csv',[
    ['模式','股票代码','股票名称',...metrics.map(([,label,unit])=>label+'（'+unit+'）'),'行情时间','采集时间','来源','来源链接','快照状态'],
    ...(result?.items??[]).map(r=>[mode,r.code,r.name,...metrics.map(([key,,,factor])=>r[key]==null?null:r[key]!/factor),
      r.market_time,packet?.fetched_at,packet?.source,packet?.source_url,packet?.is_stale?'缓存或滞后':'当前采集快照'])
  ]);
  return <section id="screen" className="panel research" hidden={!visible}>
    <header className="research-head"><div><div className="kicker">03 / CONDITION SCREENER</div><h2>条件选股</h2>
      <p>先选范围，再组合指标区间。结果必须同时满足所有已填写条件。</p></div><span className="badge">{mode==='demo'?'演示样本':'行情快照'}</span></header>
    <form className="screener-form" onSubmit={submit}>
      <div className="research-bar">
        <label>选股范围<select aria-label="选股范围" value={scope} onChange={e=>setScope(e.target.value as Scope)}>
          <option value="market">{mode==='demo'?'全部演示样本':'沪深北 A 股'}</option><option value="watch">我的自选</option>
          {board&&<option value="board">{board.name}成分</option>}
        </select></label>
        <label>交易所<select value={exchange} onChange={e=>setExchange(e.target.value)}><option value="all">全部</option><option value="sh">沪市</option><option value="sz">深市</option><option value="bj">北交所</option></select></label>
        <label className="research-checkbox"><input type="checkbox" checked={excludeRisk} onChange={e=>setExcludeRisk(e.target.checked)}/>排除 ST 和退市整理</label>
      </div>
      <div className="research-presets"><span>常用条件</span>{presets.map(p=><button className="btn" type="button" key={p.label} onClick={()=>{setRanges(p.ranges);setSort(p.sort);setDirection('desc')}}>{p.label}</button>)}
        <button className="btn" type="button" onClick={()=>setRanges({})}>清空区间</button></div>
      <div className="filter-grid">{metrics.map(([key,label,unit])=><fieldset key={key}><legend>{label} <span>{unit}</span></legend>
        <input aria-label={label+'下限'} type="number" step="any" placeholder="不限下限" value={ranges[key]?.minimum??''} onChange={e=>changeRange(key,'minimum',e.target.value)}/>
        <span>—</span><input aria-label={label+'上限'} type="number" step="any" placeholder="不限上限" value={ranges[key]?.maximum??''} onChange={e=>changeRange(key,'maximum',e.target.value)}/>
      </fieldset>)}</div>
      <div className="research-bar screen-actions"><label>排序<select value={sort} onChange={e=>setSort(e.target.value as MetricKey)}>{metrics.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
        <label>顺序<select value={direction} onChange={e=>setDirection(e.target.value)}><option value="desc">从高到低</option><option value="asc">从低到高</option></select></label>
        <label>条数<select value={limit} onChange={e=>setLimit(Number(e.target.value))}>{[30,50,100].map(n=><option key={n}>{n}</option>)}</select></label>
        <button className="btn research-primary" type="submit">运行筛选</button>
        {formError&&<span className="research-error" role="alert">{formError}</span>}
      </div>
    </form>
    {result?<><div className="screen-summary" aria-live="polite"><strong>符合 {result.matched} 只</strong><span>显示 {result.items.length} · 扫描 {result.scanned} · 有效标的 {result.eligible} · 条件字段缺失 {result.missing}</span>
      <button className="btn" disabled={!result.items.length} onClick={exportResults}>导出结果 CSV</button></div>
      {result.items.length?<div className="table-wrap research-table"><table><thead><tr><th>股票</th>{metrics.map(([key,label])=><th key={key}>{label}</th>)}<th>操作</th></tr></thead><tbody>
        {result.items.map(r=><tr key={r.code}><td><button className="plain row-link" onClick={()=>openChart(r.code)}>{r.name}<small>{r.code.toUpperCase()}</small></button></td>
          {metrics.map(([key,,,factor])=><td key={key} className={key==='pct'||key==='main_net'?tone(r[key]):undefined}>
            {factor===1e8?money(r[key],key==='main_net'):key==='pct'||key==='turnover'?percent(r[key]):fmt(r[key])}</td>)}
          <td className="screen-row-actions"><button className="btn" disabled={watch.includes(r.code)} onClick={()=>setFeedback(addWatch(r.code)??'已加入自选 · '+r.name)}>{watch.includes(r.code)?'已自选':'加自选'}</button>
            <button className="btn" disabled={tracked.some(t=>t.code===r.code)} onClick={()=>setFeedback(addTrack(r.code)??'已加入追踪 · '+r.name)}>{tracked.some(t=>t.code===r.code)?'已追踪':'追踪'}</button></td></tr>)}
      </tbody></table></div>:<div className="empty research-empty">没有满足所有条件的股票。</div>}</>:<div className="empty research-empty">
        {!body?'设置条件后点击「运行筛选」。':packet?.error==='后台采集中'?'后台正在采集完整行情，首轮可能需要数分钟。':
          packet?.error||'正在读取行情快照…'}</div>}
    {feedback&&<div className="research-foot screen-feedback" role="status">{feedback}</div>}
    <ResearchSource packet={packet}/>
    <div className="research-foot">区间包含上下限。金额按亿元填写，涨跌幅与换手率按百分数填写。缺失指定字段的标的不参与匹配。</div>
  </section>;
}
