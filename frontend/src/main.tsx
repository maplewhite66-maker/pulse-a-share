import {lazy,Suspense,useCallback,useEffect,useRef,useState,type ReactNode,type SetStateAction} from 'react';
import {createRoot} from 'react-dom/client';
import {Screener,Tracking} from './research';
import {BuyCandidates} from './buy-candidates';
import {WeightedSectorTreemap} from './weighted-sector-treemap';
import {newTrack,TRACK_LIMIT,type Tracked} from './tracking';
import {appendWatchCodes,mergeObservationPools,POOLED_QUOTE_LIMIT,readObservationPool,WATCH_LIMIT} from './observation-pools';
import {externalUrl,csv,fmt,money,percent,signed,ticker,timeText,tone,latestPacket,useFeed,useSaved,useStream,
  type Alert,type Bars,type Clock,type Flow,type Mode,type Mood,type News,type Packet,type Quote,type Ranking,type RoleData,type Score,type Sector,type Tail} from './data';
import './style.css';

const LiveStrategy=lazy(()=>import('./live-strategy').then(module=>({default:module.LiveStrategy})));
const Premarket=lazy(()=>import('./premarket').then(module=>({default:module.Premarket})));
const NextSessionWatch=lazy(()=>import('./next-session-watch').then(module=>({default:module.NextSessionWatch})));
const TradePlans=lazy(()=>import('./trade-plans').then(module=>({default:module.TradePlans})));
const HourlyOpportunities=lazy(()=>import('./hourly-opportunities').then(module=>({default:module.HourlyOpportunities})));
const GroundVolumeTop20=lazy(()=>import('./components/GroundVolumeTop20').then(module=>({default:module.GroundVolumeTop20})));
const SectorLiveFlow=lazy(()=>import('./components/SectorLiveFlow').then(module=>({default:module.SectorLiveFlow})));
const CapitalProfilerPanel=lazy(()=>import('./components/CapitalProfilerPanel').then(module=>({default:module.CapitalProfilerPanel})));
const HotMoneySimulator=lazy(()=>import('./components/HotMoneySimulator').then(module=>({default:module.HotMoneySimulator})));
const PulseV4Strategy=lazy(()=>import('./components/PulseV4Strategy').then(module=>({default:module.PulseV4Strategy})));
const PaperTrading=lazy(()=>import('./paper-trading').then(module=>({default:module.PaperTrading})));
const GlobalMarket=lazy(()=>import('./global-market').then(module=>({default:module.GlobalMarket})));
const PriceChart=lazy(()=>import('./charts').then(module=>({default:module.PriceChart})));
const FlowChart=lazy(()=>import('./charts').then(module=>({default:module.FlowChart})));
const indices=[['sh000001','上证指数'],['sz399001','深证成指'],['sz399006','创业板指'],['sh000300','沪深300'],['sh000688','科创50']];
const seed=['sh601138','sz300750','sz002594','sh688256','sh600519','sz000001'];
const periods=[['m1','1分'],['m5','5分'],['m15','15分'],['m30','30分'],['m60','60分'],['day','日K'],['week','周K'],['month','月K']];
const kinds=[['industry','行业'],['concept','概念'],['region','地域']];
const spans=[['today','今日'],['5d','5日'],['10d','10日']];
const panelNames={watch:'自选盯盘',price:'行情图表',mood:'市场情绪',sectors:'板块资金',alerts:'异动预警',tail:'尾盘观察',news:'财经快讯'};
type PanelKey=keyof typeof panelNames;
type Settings={hidden:PanelKey[];width:'standard'|'wide';sectorFirst:boolean;newsFirst:boolean};
const defaultLayout:Settings={hidden:[],width:'standard',sectorFirst:false,newsFirst:false};
const defaultThresholds={pct:3,turnover:5,rapid:1,volume:2.5};
const researchPages=['tracking','screen','ideas','tomorrow','plan','hourly','ground-volume','paper','global','premarket','monitor',
  'sector-live','capital-profile','hot-money','pulse-v4','close-review'] as const;
type ResearchPage=typeof researchPages[number];
function researchPage(id:string){return researchPages.find(page=>page===id.split('?',1)[0])??null}

function Glyph({name,size=16}:{name:string;size?:number}){
  const path:Record<string,string>={pulse:'M2 12h5l2.8-6 4 12 2.5-6H22',grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    layers:'M12 3 3 8l9 5 9-5-9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',star:'m12 3 2.7 5.5 6 .9-4.3 4.2 1 6-5.4-2.8-5.4 2.8 1-6L3.3 9.4l6-.9L12 3Z',
    bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4',clock:'M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm0 5v6l4 2',
    refresh:'M20 7a8.5 8.5 0 1 0 1 8M20 3v5h-5',full:'M3 9V3h6m6 0h6v6m0 6v6h-6m-6 0H3v-6',
    settings:'M3 6h18M3 12h18M3 18h18M8 4v4m8 2v4m-7 2v4',
    target:'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Zm0 5a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm0 4h.01',
    globe:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0 0c5-3 5-17 0-20-5 3-5 17 0 20ZM2 12h20',
    filter:'M3 5h18l-7 8v5l-4 2v-7L3 5Z',search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6',chev:'m9 5 7 7-7 7'};
  return <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round"><path d={path[name]||path.pulse}/></svg>;
}
function Panel({id,kicker,title,actions,children,className=''}:{id?:string;kicker:string;title:ReactNode;actions?:ReactNode;children:ReactNode;className?:string}){
  return <section className={'panel '+className} id={id}><header className="panel-head"><div><div className="kicker">{kicker}</div><h2>{title}</h2></div>{actions}</header>{children}</section>;
}
function Tabs({items,value,onChange,label}:{items:string[][];value:string;onChange:(v:string)=>void;label:string}){
  return <div className="tabs" role="group" aria-label={label}>{items.map(([key,name])=><button key={key} type="button" aria-pressed={key===value} className={key===value?'active':''} onClick={()=>onChange(key)}>{name}</button>)}</div>;
}
function Empty({text='未接入或暂无数据'}:{text?:string}){return <div className="empty">{text}</div>}
function Source({p}:{p:Partial<Packet<unknown>>|null}){
  if(!p)return <div className="source">等待读取数据</div>;
  return <div className={'source'+(p.error?' bad':'')}>
    <span>{p.source_url&&p.source?<a href={externalUrl(p.source_url)} target="_blank" rel="noreferrer">{p.source} ↗</a>:p.source||'未接入'}</span>
    {p.market_time&&<span>行情 {timeText(p.market_time)}</span>}
    {p.fetched_at&&<span>采集 {timeText(p.fetched_at)}</span>}
    {p.is_stale&&p.data!=null&&<b>缓存或时间滞后</b>}{p.error&&<b>{p.error}</b>}
    {p.note&&<span>{p.note}</span>}
  </div>;
}

const flowStatus:Record<string,string>={intraday:'盘中快照',lunch:'午间保留上午快照',closed_session:'最近交易日收盘',
  daily:'日线',unavailable:'数据不可用',cached_stale:'断源 · 缓存快照',calendar_unknown:'交易日历待确认',
  wrong_session:'日期不符',delayed:'源端时间滞后',incomplete_close:'收盘序列未更新'};

function StockFlowSummary({packet}:{packet:Packet<Flow[]>|null}){
  const meta=packet?.flow;
  if(!meta)return null;
  const latest=packet.data?.at(-1);
  return <div className="research-bar">
    <span>{meta.trade_day??'日期待确认'} · {flowStatus[meta.state]??meta.state} · {meta.point_count} 点</span>
    {meta.delay_endpoint&&<span className="muted">延时入口</span>}
    <span>主力净额 {fmt(latest?.main_net)} 元 · {meta.cumulative?'当日累计':'日净额'}</span>
    <span className="muted">大单 {fmt(latest?.large_net)} 元 · 超大单 {fmt(latest?.super_net)} 元</span>
    {meta.cumulative&&meta.state==='closed_session'&&<span className="muted">{meta.complete?'全天序列':'分钟覆盖不全'}</span>}
  </div>;
}
function Explain({score,label='评分明细'}:{score:Score;label?:string}){
  return <details className="explain"><summary>{label} <b>{fmt(score.score,1)}</b></summary>
    <div className="muted">有效权重 {fmt(score.coverage*100,0)}%</div>
    {score.parts.map(p=><div className="explain-row" key={p.label}><span>{p.label}</span><span>{fmt(p.score,1)} · 权重 {p.weight}%</span></div>)}
  </details>;
}
function Metric({label,value,cls}:{label:string;value:ReactNode;cls?:string}){return <div className="metric"><span>{label}</span><strong className={cls}>{value}</strong></div>}
function CapLine({label,value}:{label:ReactNode;value:ReactNode}){return <div className="capline"><span>{label}</span><b>{value}</b></div>}
function timeNow(){
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hourCycle:'h23',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date());
}
function QuoteClock({sample,cadence,stale}:{sample?:string|null;cadence:number;stale:boolean}){
  const [time,setTime]=useState(timeNow);
  const [stamp,setStamp]=useState(Date.now);
  useEffect(()=>{const id=setInterval(()=>{setTime(timeNow());setStamp(Date.now())},1000);return ()=>clearInterval(id)},[]);
  const age=sample?Math.max(0,Math.round((stamp-Date.parse(sample))/1000)):null;
  return <div className="clock quote-clock"><span>北京时间 UTC+8</span><strong>{time}</strong>
    <small title="本地采集时间与交易所行情时间分开显示" className={stale?'quote-delayed':''}>{age===null?'等待报价':stale?'报价滞后':`采集 ${age} 秒前`} · {cadence} 秒周期</small></div>;
}
function App(){
  const [mode,setMode]=useSaved<Mode>('mode-v3','live');
  const config=useFeed<{allow_demo:boolean;cloud:boolean}>('/api/config','live',0,0);
  const canDemo=config?.data?.allow_demo??false;
  useEffect(()=>{if(config?.data&&!config.data.allow_demo&&mode!=='live')setMode('live')},[config,mode,setMode]);
  const [watch,setWatch]=useSaved<string[]>('watch',seed);
  const [tracking,setTracking]=useSaved<Record<Mode,Tracked[]>>('tracked',{demo:[],live:[]});
  const [research,setResearch]=useState<ResearchPage|null>(()=>researchPage(location.hash.slice(1)));
  const [draftPrice,setDraftPrice]=useState<number>();
  useEffect(()=>{
    function openReview(){
      if(!location.hash.startsWith('#plan?review='))return;
      setActiveNav('plan');setResearch('plan');setDraftPrice(undefined);
    }
    window.addEventListener('hashchange',openReview);
    return ()=>window.removeEventListener('hashchange',openReview);
  },[]);
  const [screenBoard,setScreenBoard]=useState<{code:string;name:string;visit:number}|null>(null);
  const [trackFocus,setTrackFocus]=useState<{code:string;visit:number}|null>(null);
  const tracked=tracking[mode];
  const setTracked=useCallback((action:SetStateAction<Tracked[]>)=>{
    setTracking(store=>{
      const next=typeof action==='function'?action(store[mode]):action;
      return next===store[mode]?store:{...store,[mode]:next};
    });
  },[mode,setTracking]);
  const [layout,setLayout]=useSaved<Settings>('layout',defaultLayout);
  const [thresholds,setThresholds]=useSaved('thresholds',defaultThresholds);
  const [readNews,setReadNews]=useSaved<string[]>('newsRead',[]);
  const [newsTab,setNewsTab]=useState('news');
  const [client]=useSaved('client',globalThis.crypto?.randomUUID?.()||'client-'+Math.random().toString(36).slice(2));
  const [selected,setSelected]=useState(seed[0]), [period,setPeriod]=useState('day'), [adjust,setAdjust]=useState('qfq');
  const [view,setView]=useState<'kline'|'line'|'macd'|'rsi'>('kline');
  const [kind,setKind]=useState('industry'),[span,setSpan]=useState('today'),[order,setOrder]=useState('inflow');
  const [sectorView,setSectorView]=useState('table'),[board,setBoard]=useState<string|null>(null);
  const [flowSpan,setFlowSpan]=useState('minute'),[detail,setDetail]=useState('summary');
  const [input,setInput]=useState(''),[watchError,setWatchError]=useState('');
  const [revision,setRevision]=useState(0),[settings,setSettings]=useState(false),[activeNav,setActiveNav]=useState<string>(()=>researchPage(location.hash.slice(1))??'top');
  const [audio,setAudio]=useState(false),[notify,setNotify]=useState(false);
  const audioContext=useRef<AudioContext|null>(null),lastAlert=useRef<Record<string,string>>({});
  const fileInput=useRef<HTMLInputElement>(null);
  const poolInput=useRef<HTMLInputElement>(null);
  const currentPools=useRef({watch,tracking});
  currentPools.current={watch,tracking};
  const codes=Array.from(new Set([selected,...watch,...tracked.map(r=>r.code)])).sort().join(',');
  const watched=watch.join(',');
  const stream=useStream(mode,codes,revision);
  const health=useFeed<Clock>(stream.online?null:'/api/system/health',mode,30_000,revision);
  const clock=stream.clock??health?.data;
  const cadence=clock?.quote_interval_s??(mode==='demo'?5:8);
  const quoteInterval=cadence*1000;
  const manual=revision>0?'&refresh=1':'';
  const indexFeed=useFeed<Quote[]>(!stream.online?(revision>0?'/api/market/indices?refresh=1':'/api/market/indices'):null,mode,quoteInterval,revision);
  const quoteFeed=useFeed<Quote[]>(!stream.online?'/api/stocks/quotes?codes='+codes+manual:null,mode,quoteInterval,revision);
  const indexData=stream.online?stream.indices??null:latestPacket(stream.indices,indexFeed);
  const quotes=stream.online?stream.quotes??null:latestPacket(stream.quotes,quoteFeed);
  const quoteByCode=new Map(quotes?.data?.map(q=>[q.code,q]));
  const indexByCode=new Map(indexData?.data?.map(q=>[q.code,q]));
  const current=quoteByCode.get(selected)||indexByCode.get(selected);
  const qpath='/api/stocks/'+selected;
  const stockSelected=!indices.some(i=>i[0]===selected);
  const inOverview=research===null;
  const chart=useFeed<Bars>(inOverview&&showPanel('price')?qpath+'/kline?period='+period+'&adjust='+adjust:null,mode,clock?.active?(periods.slice(0,5).some(([key])=>key===period)?30_000:45_000):240_000,revision);
  const stockFlow=useFeed<Flow[]>(inOverview&&showPanel('price')&&stockSelected&&detail==='flow'?qpath+'/fund-flow?span='+flowSpan:null,mode,clock?.active?90_000:300_000,revision);
  const search='kind='+kind+'&period='+span+'&order='+order;
  const ranking=useFeed<Ranking>(inOverview&&showPanel('sectors')?'/api/sectors/ranking?'+search:null,mode,clock?.tail?60_000:clock?.active?90_000:300_000,revision);
  const role=useFeed<RoleData>(inOverview&&showPanel('sectors')&&board?'/api/sectors/'+board+'/roles?'+search:null,mode,300_000,revision);
  const sectorFlow=useFeed<Flow[]>(inOverview&&showPanel('sectors')&&board?'/api/sectors/fund-flow?'+search+'&board='+board:null,mode,90_000,revision);
  const mood=useFeed<Mood>(inOverview&&showPanel('mood')?'/api/market/sentiment':null,mode,90_000,revision);
  const tail=useFeed<Tail>(inOverview&&showPanel('tail')?'/api/tail?codes='+watched:null,mode,clock?.tail?15_000:90_000,revision);
  const news=useFeed<News[]>(inOverview&&showPanel('news')?(newsTab==='news'?'/api/news':qpath+'/announcements'):null,mode,newsTab==='news'?180_000:600_000,revision);
  const rules=new URLSearchParams(Object.entries(thresholds).map(([k,v])=>[k,String(v)]));
  const alerts=useFeed<Alert[]>('/api/alerts?codes='+watched+'&client='+client+'&kind='+kind+'&'+rules,mode,quoteInterval,revision);
  const rows=ranking?.data?.items??[];
  const bars=chart?.data?.items??[];
  const lastBar=bars.at(-1);
  const moodData=mood?.data;
  const sector=rows.find(s=>s.code===board);
  const demo=mode==='demo';
  function showPanel(k:PanelKey){return !layout.hidden.includes(k)}
  const show=showPanel;
  const choose=(c:string)=>{setSelected(c);setActiveNav('price')};
  const navigate=(id:string)=>{
    setActiveNav(id);
    const page=researchPage(id);
    setResearch(page);
    setDraftPrice(undefined);
    if(page||researchPage(location.hash.slice(1)))history.replaceState(null,'',location.pathname+location.search+(page?'#'+page:''));
    requestAnimationFrame(()=>document.getElementById(id)?.scrollIntoView({behavior:'smooth',block:'start'}));
  };
  const openChart=(code:string)=>{
    choose(code);
    if(!show('price'))setLayout(old=>({...old,hidden:old.hidden.filter(panel=>panel!=='price')}));
    navigate('price');
  };
  const openCapitalProfile=(code:string)=>{
    const normalized=ticker(code);
    if(!normalized||indices.some(([id])=>id===normalized))return;
    setSelected(normalized);
    navigate('capital-profile');
  };
  const openSectorStock=(code:string)=>{
    const normalized=ticker(code);
    if(normalized)openChart(normalized);
  };
  const addTrack=(code:string)=>{
    if(indices.some(([id])=>id===code))return '请选择股票代码，指数可在总览中查看。';
    if(tracked.some(r=>r.code===code))return '已在当前模式的追踪列表';
    if(tracked.length>=TRACK_LIMIT)return '追踪列表最多 '+TRACK_LIMIT+' 只';
    if(!watch.includes(code)&&new Set([...watch,...tracked.map(r=>r.code)]).size>=POOLED_QUOTE_LIMIT)
      return '自选和追踪合计已达 '+POOLED_QUOTE_LIMIT+' 只';
    setTracked(old=>old.some(r=>r.code===code)||old.length>=TRACK_LIMIT?old:[...old,newTrack(code,quoteByCode.get(code)?.name)]);
    return null;
  };
  const trackCurrent=()=>{
    const error=addTrack(selected);
    if(error&&!tracked.some(r=>r.code===selected)){setWatchError(error);return}
    setTrackFocus(old=>({code:selected,visit:(old?.visit??0)+1}));
    navigate('tracking');
  };
  const addWatch=(code:string)=>{
    if(watch.includes(code))return '已在自选池';
    if(watch.length>=WATCH_LIMIT)return '自选池最多保存 '+WATCH_LIMIT+' 只';
    if(appendWatchCodes(watch,[code],tracking).skipped)return '自选和追踪合计已达 '+POOLED_QUOTE_LIMIT+' 只';
    setWatch(old=>appendWatchCodes(old,[code],tracking).watch);
    return null;
  };
  const screenSelectedBoard=()=>{
    if(!sector)return;
    setScreenBoard(old=>({code:sector.code,name:sector.name,visit:(old?.visit??0)+1}));
    navigate('screen');
  };

  useEffect(()=>{
    const a=alerts?.data?.[0];
    if(!a)return;
    const key=mode;
    const previous=lastAlert.current[key];
    lastAlert.current[key]=a.id;
    if(!previous||previous===a.id)return;
    if(audio&&audioContext.current){
      const ac=audioContext.current,osc=ac.createOscillator(),gain=ac.createGain();
      osc.frequency.value=720;gain.gain.value=.055;osc.connect(gain);gain.connect(ac.destination);osc.start();osc.stop(ac.currentTime+.18);
    }
    if(notify&&'Notification' in window&&Notification.permission==='granted'){
      new Notification((demo?'演示 · ':'')+a.event,{body:a.name+' '+signed(a.value)+' '+a.unit,tag:a.code+a.event});
    }
  },[alerts,mode,audio,notify,demo]);
  const add=(raw:string)=>{
    const values=raw.split(/[\s,;，；]+/).map(s=>s.replace(/^"|"$/g,'')).filter(Boolean);
    const valid=values.map(ticker).filter((c):c is string=>c!==null);
    if(!valid.length){setWatchError('请输入股票代码，例如 600519、000001 或 sh000001');return}
    const next=appendWatchCodes(watch,valid,tracking);
    setWatch(next.watch);
    if(next.watch.includes(valid[0]))choose(valid[0]);
    setInput('');
    setWatchError(next.skipped?'自选或合计容量已满，跳过 '+next.skipped+' 只。':valid.length<values.length?'已加入有效代码，跳过了无法识别的字段':'');
  };
  async function importObservationPools(file:File){
    if(file.size>1_000_000){setWatchError('文件上限 1 MB');return}
    try{
      const pool=readObservationPool(await file.text());
      const {watch:existing,tracking:tracks}=currentPools.current;
      const result=mergeObservationPools(existing,tracks,pool);
      if(result.watchAdded)setWatch(result.watch);
      if(result.trackAdded)setTracking(old=>({...old,live:result.tracked}));
      setWatchError('真实模式自选新增 '+result.watchAdded+' 只，个股追踪新增 '+result.trackAdded+' 只'+
        (result.skipped?'，因容量跳过 '+result.skipped+' 只。':'。'));
    }catch(error){
      setWatchError(error instanceof SyntaxError?'无法解析观察池 JSON。':error instanceof Error?error.message:'无法读取双池文件。');
    }
  }
  const exportWatch=()=>csv('Pulse-watchlist.csv',[['code'],...watch.map(c=>[c])]);
  const exportAlerts=()=>csv('Pulse-alerts-'+mode+'.csv',[['模式','标的','事件','数值','阈值','单位','时间','数据源','来源链接'],...(alerts?.data??[]).map(a=>[a.mode,a.code,a.event,a.value,a.threshold,a.unit,a.market_time,a.source,a.source_url])]);
  const chooseSector=(code:string)=>setBoard(board===code?null:code);
  const symbols=watch.map(code=>({code,q:quoteByCode.get(code)}));
  const gainers=moodData?.up,losers=moodData?.down;
  const total=gainers!=null&&losers!=null?gainers+losers:0;
  const upShare=gainers!=null&&total?gainers/total:0;
  const topAlerts=alerts?.data??[];
  const messages=[indexData,quotes,ranking,chart,stockFlow].filter(p=>p?.error).length;
  const mainWarnings=demo?'演示模式 · 所有价格、资金、情绪和预警均为离线样本。':messages? '真实模式 · '+messages+' 项行情请求异常，相关面板会显示失败原因或上次真实快照。':'真实模式 · 交易状态和数据时间以各面板的来源标记为准。';
  const moodPanel=<Panel id="mood" kicker="MARKET BREADTH" title="市场情绪" actions={<span className="badge">全市场</span>}>
    <div className="mood-top"><div><span className="muted">情绪评分 / 100</span><strong>{fmt(moodData?.heat.score,0)}</strong></div>
      <div className="ring" style={{background:'conic-gradient(var(--rise) '+(upShare*100)+'%, '+(total?'var(--fall)':'var(--line)')+' 0)'}}><div>{total?fmt(upShare*100,0)+'%':'—'}<small>上涨占比</small></div></div></div>
    <div className="breadth"><span className="rise">上涨 {fmt(gainers,0)}</span><span>平盘 {fmt(moodData?.flat,0)}</span><span className="fall">下跌 {fmt(losers,0)}</span></div>
    <div className="breadthbar"><i style={{width:(upShare*100)+'%'}}/></div>
    <div className="metric-grid"><Metric label="涨停" value={fmt(moodData?.limit_up,0)} cls="rise"/><Metric label="跌停" value={fmt(moodData?.limit_down,0)} cls="fall"/>
      <Metric label="最高连板" value={fmt(moodData?.streak,0)}/><Metric label="炸板率" value={moodData?.break_rate==null?'—':fmt(moodData.break_rate,1)+'%'}/>
      <Metric label="昨日涨停今表现" value={percent(moodData?.premium)} cls={tone(moodData?.premium)}/><Metric label="市场成交额" value={money(moodData?.amount)}/></div>
    <div className="panel-pad">{moodData&&<Explain score={moodData.heat}/>}
      <details className="explain"><summary>指标来源与覆盖</summary>
        <div className="muted">有效报价 {fmt(moodData?.covered,0)} / {fmt(moodData?.total,0)} · 强势股（两连板及以上） {fmt(moodData?.strong,0)} · 炸板 {fmt(moodData?.broken,0)}</div>
        {Object.entries(moodData?.sources??{}).map(([k,p])=><div key={k}><span className="micro">{{breadth:'涨跌广度',up:'涨停池',down:'跌停池',broken:'炸板池',yesterday:'昨日涨停池'}[k]}</span><Source p={p}/></div>)}
      </details>
    </div>
  </Panel>;
  const alertsPanel=<Panel id="alerts" kicker="SIGNAL CENTER" title="异动预警" actions={<span className="badge">{topAlerts.length} 条</span>}>
    <div className="panel-pad"><details className="explain"><summary>阈值与提醒</summary>
      <div className="thresholds">{(Object.keys(defaultThresholds) as (keyof typeof defaultThresholds)[]).map((k,i)=><label key={k}>{['涨跌幅 %','换手率 %','短时涨跌 %','量比 倍'][i]}<input aria-label={['涨跌幅阈值','换手率阈值','短时涨跌阈值','量比阈值'][i]} type="number" min={k==='volume'?1:.1} max={k==='pct'?50:k==='rapid'?25:100} step=".1" defaultValue={thresholds[k]} onBlur={e=>{if(e.currentTarget.checkValidity()&&e.currentTarget.value)setThresholds({...thresholds,[k]:Number(e.currentTarget.value)});else e.currentTarget.value=String(thresholds[k])}}/></label>)}</div>
      <div className="button-row"><button className={audio?'btn chosen':'btn'} onClick={()=>{if(!audio){audioContext.current??=new AudioContext();void audioContext.current.resume()}setAudio(!audio)}}>{audio?'声音已开':'启用声音'}</button>
      <button className={notify?'btn chosen':'btn'} onClick={()=>{if(notify){setNotify(false);return}if('Notification' in window)void Notification.requestPermission().then(p=>setNotify(p==='granted'))}}>{notify?'通知已开':'启用通知'}</button><button className="btn" onClick={exportAlerts}>导出 CSV</button></div>
    </details></div>
    <div className="alerts-list">{topAlerts.length?topAlerts.slice(0,20).map(a=><article className="alert" key={a.id}>
      <div className={'alert-icon '+tone(a.value)}><Glyph name="pulse"/></div><div><strong>{a.name} <span>{a.event}</span></strong><p><b className={tone(a.value)}>{signed(a.value)} {a.unit}</b> · 阈值 {fmt(a.threshold)} {a.unit}{a.previous!=null?' · 参考值 '+fmt(a.previous):''}</p>
      <small>{demo?'演示 · ':''}{timeText(a.market_time)} · {a.source}</small></div></article>):<Empty text={alerts?.error||'暂无预警，交易时段将扫描自选报价'}/>}</div>
  </Panel>;
  const newsPanel=<Panel id="news" kicker="MARKET WIRE" title="快讯与公告" actions={<Tabs label="资讯类型" items={[['news','快讯'],['announcements','公告']]} value={newsTab} onChange={setNewsTab}/>}>
    <div className="news-list">{(news?.data??[]).slice(0,12).map(n=>{
      const id=mode+':'+n.id,read=readNews.includes(id);
      return <article className={read?'is-read':''} key={n.id}><small>{timeText(n.time)}</small><p>{n.url?<a href={externalUrl(n.url)} target="_blank" rel="noreferrer" onClick={()=>setReadNews([...new Set([...readNews,id])].slice(-200))}>{n.title} ↗</a>:n.title}</p>
        <button className="plain" disabled={read} onClick={()=>setReadNews([...readNews,id].slice(-200))}>{read?'已读':'标为已读'}</button></article>
    })}{!news?.data?.length&&<Empty text={news?.error||'等待读取快讯'}/>}</div><Source p={news}/>
  </Panel>;
  return <div className="shell" id="top">
    <aside className="rail"><button className="logo" aria-label="回到总览" onClick={()=>navigate('top')}><Glyph name="pulse" size={27}/></button>
      <nav>{[['top','grid','总览'],['premarket','globe','盘前报告'],['watch','star','自选'],['sectors','layers','板块'],
        ['sector-live','layers','资金动态'],['capital-profile','target','资金画像'],['hot-money','pulse','风格回测'],['pulse-v4','filter','四维融合'],
        ['alerts','bell','预警'],['monitor','clock','全天策略'],['tracking','target','追踪'],['plan','layers','交易复盘'],['screen','filter','选股'],['ground-volume','filter','地量严选'],['hourly','clock','超短线'],['paper','pulse','模拟交易'],['ideas','pulse','开收盘'],['close-review','clock','收盘复盘'],['global','globe','全球行情'],['tomorrow','star','明日关注'],['price','pulse','走势'],['tail','clock','尾盘']].filter(([id])=>canDemo||id!=='paper').map(([id,icon,text])=><button key={id} className={id===activeNav?'active':''} onClick={()=>navigate(id)}><Glyph name={icon} size={18}/><small>{text}</small></button>)}</nav>
      <span className="rail-version">PULSE 4.0</span></aside>
    <main className="workspace">
      <header className="top"><div className="brand"><div className="kicker"><span className="tag-line"/> PULSE <span className="sep">/</span> MARKET TERMINAL</div><h1>A 股智能盯盘台</h1></div>
      <div className="top-actions"><QuoteClock sample={quotes?.fetched_at??indexData?.fetched_at} cadence={cadence} stale={Boolean(quotes?.is_stale)}/>
        <span className={'session '+(clock?.active?'active':'')}><i/>{clock?.label??'交易状态查询中'}</span>
        {canDemo&&<Tabs label="数据模式" items={[[ 'demo','演示'],['live','真实']]} value={mode} onChange={v=>{if(v!==mode){setMode(v as Mode);setBoard(null);setScreenBoard(null);setTrackFocus(null)}}}/>}
        <button className="icon" title="刷新行情" aria-label="刷新行情" onClick={()=>setRevision(r=>r+1)}><Glyph name="refresh"/></button>
        <button className="icon hide-mobile" title="全屏" aria-label="全屏" onClick={()=>void (document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen()).catch(()=>{})}><Glyph name="full"/></button>
        <button className={'icon '+(settings?'chosen':'')} title="面板设置" aria-label="面板设置" onClick={()=>setSettings(!settings)}><Glyph name="settings"/></button>
      </div></header>
      <div className="content">
        {settings&&<div className="settings"><div><strong>工作台布局</strong><div className="settings-options">
          {Object.entries(panelNames).map(([k,label])=><label key={k}><input type="checkbox" checked={show(k as PanelKey)} onChange={()=>setLayout({...layout,hidden:show(k as PanelKey)?[...layout.hidden,k as PanelKey]:layout.hidden.filter(x=>x!==k)})}/>{label}</label>)}
          <label><input type="checkbox" checked={layout.width==='wide'} onChange={e=>setLayout({...layout,width:e.target.checked?'wide':'standard'})}/>加宽图表</label>
          <label><input type="checkbox" checked={layout.sectorFirst} onChange={e=>setLayout({...layout,sectorFirst:e.target.checked})}/>板块排在图表上方</label>
          <label><input type="checkbox" checked={layout.newsFirst} onChange={e=>setLayout({...layout,newsFirst:e.target.checked})}/>快讯排在预警上方</label></div></div><button className="btn" onClick={()=>setLayout(defaultLayout)}>恢复默认</button></div>}
        <div className={'notice '+(research==='tomorrow'?'':demo?'demo':messages?'warn':'')}><span className="notice-indicator"/>
          <span>{research==='tomorrow'?'收盘复盘 · 本页使用注明日期的真实收盘快照。':mainWarnings}</span>
          <small>{research==='tomorrow'?'CLOSE SNAPSHOT':demo?'OFFLINE DEMO':stream.online?'WS 已连接':'HTTP 轮询'}</small></div>
        <Tracking mode={mode} items={tracked} setItems={setTracked} active={Boolean(clock?.active)} packet={quotes}
          visible={research==='tracking'} add={addTrack} openChart={openChart} focus={trackFocus}/>
        <Screener mode={mode} watch={watch} tracked={tracked} board={screenBoard} visible={research==='screen'}
          revision={revision} openChart={openChart} addWatch={addWatch} addTrack={addTrack}/>
        <BuyCandidates key={research==='close-review'?'close-review':'ideas'} startInArchive={research==='close-review'}
          mode={mode} clock={clock} visible={research==='ideas'||research==='close-review'} revision={revision}
          watch={watch} tracked={tracked} openChart={openChart} addWatch={addWatch} addTrack={addTrack}/>
        <Suspense fallback={<div className="panel"><Empty text="正在加载工作台…"/></div>}>
        {research==='monitor'&&<LiveStrategy revision={revision} openChart={openChart}
          openFlow={code=>{setMode('live');openChart(code);setFlowSpan('minute');setDetail('flow')}} onIdeas={()=>navigate('ideas')}/>}
        {research==='premarket'&&<Premarket revision={revision} openChart={openChart}/>}
        {research==='global'&&<GlobalMarket mode={mode} revision={revision} onLive={()=>{setMode('live');setBoard(null);setScreenBoard(null);setTrackFocus(null)}} onIdeas={()=>navigate('ideas')}/>}
        {research==='tomorrow'&&<NextSessionWatch today={clock?.time.slice(0,10)} watch={watch} addWatch={addWatch}
          openChart={code=>{setMode('live');setBoard(null);setScreenBoard(null);setTrackFocus(null);openChart(code)}}/>}
        {research==='plan'&&<TradePlans mode={mode} code={stockSelected?selected:(watch[0]??seed[0])}
          quote={quoteByCode.get(stockSelected?selected:(watch[0]??seed[0]))} watch={watch} draftPrice={draftPrice}
          choose={code=>{setSelected(code);setDraftPrice(undefined)}} openChart={openChart}/>}
        {research==='hourly'&&<HourlyOpportunities mode={mode} revision={revision} openChart={openChart}
          openPlan={(code,price)=>{setSelected(code);navigate('plan');setDraftPrice(price)}}/>}
        {research==='ground-volume'&&<GroundVolumeTop20 mode={mode} revision={revision} addWatch={addWatch} addTrack={addTrack}
          openChart={(code,chartView)=>{setView(chartView);setPeriod(chartView==='line'?'m1':'day');openChart(code)}}/>}
        {research==='sector-live'&&<SectorLiveFlow mode={mode} revision={revision} openStock={openSectorStock}/>}
        {research==='capital-profile'&&<CapitalProfilerPanel mode={mode} revision={revision} code={stockSelected?selected:(watch[0]??seed[0])} openChart={openChart}/>}
        {research==='hot-money'&&<HotMoneySimulator mode={mode} revision={revision} openChart={openChart}/>}
        {research==='pulse-v4'&&<PulseV4Strategy mode={mode} revision={revision} openChart={openChart} openProfile={openCapitalProfile}/>}
        {research==='paper'&&<PaperTrading revision={revision} openChart={code=>{setMode('live');openChart(code)}}/>}
        </Suspense>
        <div className="overview" hidden={research!==null}>
        <div className="market-head"><div><div className="kicker">01 / MARKET OVERVIEW</div><h2>主要指数 <span>市场全景</span></h2></div><div className="overview-links"><span className="muted">最近交易日 {clock?.latest_day??'待确认'}</span><button className="btn" onClick={()=>navigate('premarket')}>盘前报告 ↗</button><button className="btn" onClick={()=>navigate('global')}>全球行情</button></div></div>
        <div className="index-grid">{indices.map(([code,name],i)=>{
          const q=indexByCode.get(code),style=tone(q?.pct);
          return <button key={code} className={'index-card '+(selected===code?'selected':'')} onClick={()=>choose(code)}>
            <div className="index-label"><span>{name}</span><small>0{i+1}</small></div><strong className={style}>{fmt(q?.price)}</strong>
            <div className="index-bottom"><b className={style}>{percent(q?.pct)}</b><span>{money(q?.amount)}</span></div>
            <div className="index-foot">{q?.market_time?timeText(q.market_time).slice(11):'未提供时间'}{q?.is_stale?' · 滞后':''}</div>
          </button>
        })}</div>
        <Source p={indexData}/>
        <div className={'desk '+(layout.width==='wide'?'desk-wide':'')}>
          <div className="column left-col">
          {show('watch')&&<Panel id="watch" kicker="WATCHLIST" title="我的自选" actions={<span className="badge">{watch.length} 只</span>}>
            <form className="search" onSubmit={e=>{e.preventDefault();add(input)}}><Glyph name="search" size={14}/><input aria-label="搜索或输入股票代码" placeholder="代码搜索 / 批量添加" value={input} onChange={e=>setInput(e.target.value)}/><button aria-label="添加自选" title="添加自选">+</button></form>
            {watchError&&<div className="watch-error" role="status">{watchError}</div>}
            <div className="watch-head"><span>标的 / 代码</span><span>最新 / 涨跌</span></div>
            <div className="watch-list">{symbols.map(({code,q})=><div className={'watch-row '+(selected===code?'selected':'')} key={code}>
              <button className="watch-select" onClick={()=>choose(code)}><span><strong>{q?.name??code.slice(2)}</strong><small>{code.toUpperCase()}{q?.is_stale?' · 滞后':''}</small></span><span><b>{fmt(q?.price)}</b><small className={tone(q?.pct)}>{percent(q?.pct)}</small></span></button>
              <button className="remove" aria-label={'删除 '+(q?.name??code)} onClick={()=>setWatch(watch.filter(c=>c!==code))}>×</button></div>)}
              {!watch.length&&<Empty text="输入代码添加自选股"/>}</div>
            <div className="button-row panel-pad"><input ref={fileInput} type="file" accept=".csv,.txt,text/csv,text/plain" hidden onChange={e=>{const f=e.target.files?.[0];e.target.value='';if(f){if(f.size>1_000_000)setWatchError('文件上限 1 MB');else void f.text().then(add).catch(()=>setWatchError('无法读取导入文件'))}}}/>
              <input ref={poolInput} type="file" aria-label="观察池文件" accept=".json,application/json" hidden
                onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void importObservationPools(file)}}/>
              <button className="btn" onClick={()=>fileInput.current?.click()}>导入</button>
              <button className="btn" onClick={()=>poolInput.current?.click()}>导入双池</button>
              <button className="btn" onClick={exportWatch}>导出</button></div><Source p={quotes}/>
          </Panel>}
          {show('watch')&&<Panel kicker="QUOTE DETAILS" title="报价细节"><div className="panel-pad">
            <CapLine label="昨收" value={fmt(current?.previous_close)}/><CapLine label="今开" value={fmt(current?.open)}/>
            <CapLine label="成交量（股）" value={fmt(current?.volume,0)}/><CapLine label="量比" value={fmt(current?.vol_ratio)}/>
            <CapLine label="换手率" value={percent(current?.turnover)}/><CapLine label="振幅" value={percent(current?.amplitude)}/>
            <CapLine label="流通市值" value={money(current?.float_cap)}/><CapLine label="总市值" value={money(current?.market_cap)}/>
            <details className="explain"><summary>盘口五档</summary>
              {current?.book?.length?current.book.map((b,i)=><CapLine key={i} label={'买 '+(i+1)+' '+fmt(b.bid)+' / '+fmt(b.bid_lots,0)+' 手'} value={'卖 '+(i+1)+' '+fmt(b.ask)+' / '+fmt(b.ask_lots,0)+' 手'}/>):<Empty text="盘口五档未接入"/>}</details>
          </div></Panel>}
          </div>
          <div className={'column center-col '+(layout.sectorFirst?'sector-first':'')}>
          {show('price')&&<Panel id="price" className="price-panel" kicker="PRICE ACTION" title={<>{current?.name??indices.find(x=>x[0]===selected)?.[1]??selected.slice(2)} <small>{selected.toUpperCase()}</small></>}
            actions={<Tabs label="走势类型" items={[[ 'kline','K线'],['line','分时'],['macd','MACD'],['rsi','RSI']]} value={view} onChange={v=>{setView(v as typeof view);if(v==='line')setPeriod('m1')}}/>}>
            <div className="hero"><strong className={tone(current?.pct)}>{fmt(current?.price)}</strong><span className={tone(current?.pct)}>{percent(current?.pct)}</span><small>成交额 {money(current?.amount)}</small></div>
            <div className="chart-controls"><Tabs label="K线周期" items={periods} value={period} onChange={setPeriod}/><label className="muted">复权 <select aria-label="复权" disabled={periods.slice(0,5).some(x=>x[0]===period)||indices.some(x=>x[0]===selected)} value={adjust} onChange={e=>setAdjust(e.target.value)}><option value="qfq">前复权</option><option value="hfq">后复权</option><option value="none">不复权</option></select></label></div>
            <div className="chart-meta"><span className="ma5">MA5 {fmt(lastBar?.ma5)}</span><span className="ma20">MA20 {fmt(lastBar?.ma20)}</span>
              <span>RSI {fmt(lastBar?.rsi)}</span><span>MACD {fmt(lastBar?.macd,3)}</span><span className="chart-help">滚轮缩放 · 拖动查看</span></div>
            <div className="price-chart">{bars.length?<Suspense fallback={<Empty text="正在加载图表"/>}><PriceChart bars={bars} view={view} period={period} volumeUnit={chart?.data?.volume_unit??'手'}/></Suspense>:<Empty text={chart?.error||'正在读取价格序列'}/>}</div>
            <div className="subnav"><Tabs label="个股详情" value={detail} onChange={setDetail} items={[[ 'summary','走势摘要'],['flow','主力资金']]}/>
              {detail==='flow'&&<Tabs label="资金序列" value={flowSpan} onChange={setFlowSpan} items={[[ 'minute','当日'],['day','日级']]}/>}
              {stockSelected&&<><button className="btn" onClick={trackCurrent}>{tracked.some(r=>r.code===selected)?'查看追踪':'追踪个股'}</button>
                <button className="btn" onClick={()=>openCapitalProfile(selected)}>资金画像</button></>}</div>
            {detail==='flow'?<><StockFlowSummary packet={stockFlow}/><div className="mini-chart">{stockFlow?.data?.length?<Suspense fallback={<Empty text="正在加载图表"/>}><FlowChart rows={stockFlow.data} label={stockFlow.flow?.cumulative?'个股主力资金当日累计净额':'个股主力资金净额'}/></Suspense>:<Empty text={stockFlow?.error||'主力资金序列未接入'}/>}</div><Source p={stockFlow}/></>:
              <div className="summary-line"><span>最新 K 线收盘 <b>{fmt(lastBar?.close)}</b></span><span>成交量单位 <b>{chart?.data?.volume_unit??'—'}</b></span><span>复权 <b>{{qfq:'前复权',hfq:'后复权',none:'不复权'}[chart?.data?.adjust??'']??'—'}</b></span></div>}
            <Source p={chart}/>
          </Panel>}
          {show('sectors')&&<Panel id="sectors" className="sector-panel" kicker="CAPITAL ROTATION" title={<>板块资金 <small>TOP 20</small></>} actions={<Tabs label="板块视图" value={sectorView} onChange={setSectorView} items={[[ 'table','榜单'],['heat','热力图']]}/>}>
            <div className="sector-controls"><Tabs label="板块类别" items={kinds} value={kind} onChange={v=>{setKind(v);setBoard(null)}}/><Tabs label="资金周期" items={spans} value={span} onChange={v=>{setSpan(v);setBoard(null)}}/>
              <Tabs label="资金方向" items={[[ 'inflow','净流入'],['outflow','净流出']]} value={order} onChange={v=>{setOrder(v);setBoard(null)}}/></div>
            {inOverview&&sectorView==='heat'&&mode==='live'&&kind!=='region'&&span==='today'?
              <Suspense fallback={<Empty text="正在加载资金热力图"/>}><SectorLiveFlow mode={mode} revision={revision} openStock={openSectorStock} embedded/></Suspense>:
              rows.length?(sectorView==='heat'?<WeightedSectorTreemap sectors={rows} selected={board} onSelect={chooseSector}/>:
              <div className="table-wrap sector-table"><table><thead><tr><th>排名 / 板块</th><th>涨跌幅</th><th>主力净额</th><th>成交额</th><th>领涨股</th><th>热度</th></tr></thead><tbody>{rows.map(s=><tr key={s.code} className={board===s.code?'picked':''}>
                <td><button className="plain row-link" onClick={()=>chooseSector(s.code)}><span className="rank">{String(s.rank).padStart(2,'0')}</span>{s.name}<small className={tone(s.rank_delta)}>{s.rank_delta?signed(s.rank_delta,0):''}</small></button></td>
                <td className={tone(s.pct)}>{percent(s.pct)}</td><td className={tone(s.main_net)}>{money(s.main_net,true)}</td><td>{money(s.amount)}</td><td>{s.leader||'—'}</td><td>{fmt(s.heat.score,0)}</td></tr>)}</tbody></table></div>):
              <Empty text={ranking?.error|| (ranking?.data?'该周期没有对应方向的有效板块':'正在读取资金榜单')}/>}
            <Source p={ranking}/>
            {board&&<BoardDetail sector={sector} role={role} flow={sectorFlow} select={choose} close={()=>setBoard(null)} openScreen={screenSelectedBoard}/>}
          </Panel>}
          {show('tail')&&<Panel id="tail" kicker="CLOSING WATCH" title="尾盘观察" actions={<span className="badge amber">{clock?.tail&&!demo?'采集中':'14:45—15:00'}</span>}>
            <div className="panel-pad"><div className="tail-line"><Glyph name="clock" size={21}/><div><strong>14:57—15:00 收盘集合竞价</strong><p>虚拟匹配价、匹配量、未匹配量均为未接入。</p></div><span className="badge">{demo?'演示':clock?.label??'待确认'}</span></div>
              {tail?.data?.items.length?<div className="table-wrap"><table><thead><tr><th>标的</th><th>尾段涨跌</th><th>成交额变化</th><th>观察分</th></tr></thead><tbody>{tail.data.items.map(t=><tr key={t.code}><td><button className="plain" onClick={()=>choose(t.code)}>{t.name}</button><small className="micro">{t.from.slice(11,16)}—{t.to.slice(11,16)}</small></td><td className={tone(t.move)}>{percent(t.move)}</td><td>{money(t.amount)}</td><td><Explain label="观察分" score={t.heat}/></td></tr>)}</tbody></table></div>:<Empty text="需要两次有效尾盘快照才会计算变化"/>}
              {!!tail?.data?.sectors.length&&<div className="tail-sectors">{tail.data.sectors.map(s=><CapLine key={s.kind+s.name} label={s.name+' · '+(s.leader??'领涨未提供')} value={<span className={tone(s.change)}>{money(s.change,true)} · 排名 {signed(s.rank_delta,0)}</span>}/>)}</div>}
            </div><Source p={tail}/>
          </Panel>}
          </div>
          <div className={'column right-col '+(layout.newsFirst?'news-first':'')}>
            {show('mood')&&moodPanel}{show('alerts')&&alertsPanel}{show('news')&&newsPanel}
          </div>
        </div>
        </div>
        <footer><div><span className="notice-indicator"/> PULSE TERMINAL <span>行情研究工作台</span></div><details><summary>连接状态 / 数据说明</summary>
          <p>WebSocket {stream.online?'已连接':'等待连接'} · 深交所日历 {clock?.calendar_error||clock?.calendar_source||'演示或未提供'} · a-stock-data v3.10.4</p>
          <p>页面开启时采集和记录。资金净额为数据源估算口径。尾盘额外采集行业今日榜，专用集合竞价明细未接入。</p></details><a href="https://github.com/simonlin1212/a-stock-data" target="_blank" rel="noreferrer">数据规范 ↗</a></footer>
      </div>
    </main>
  </div>;
}

function BoardDetail({sector,role,flow,select,close,openScreen}:{sector:Sector|undefined;role:Packet<RoleData>|null;flow:Packet<Flow[]>|null;select:(c:string)=>void;close:()=>void;openScreen:()=>void}){
  return <div className="board-detail"><div className="board-detail-head"><div><div className="kicker">SECTOR DRILLDOWN</div><h3>{sector?.name??'板块'} · 成分与角色</h3></div><div className="button-row">{sector&&<button className="btn" onClick={openScreen}>筛选此板块</button>}<button className="icon" aria-label="关闭板块详情" onClick={close}>×</button></div></div>
    {sector&&<><div className="metric-grid board-metrics"><Metric label="今日净额" value={money(sector.net_1,true)}/><Metric label="5 日净额" value={money(sector.net_5,true)}/><Metric label="10 日净额" value={money(sector.net_10,true)}/></div><Explain score={sector.heat} label="板块热度"/></>}
    <div className="mini-chart">{(flow?.data?.length??0)>1?<Suspense fallback={<Empty text="正在加载图表"/>}><FlowChart rows={flow!.data!} label="板块主力净额采集快照"/></Suspense>:<Empty text="资金趋势将在累积两次快照后显示"/>}</div>
    {role?.data?<><div className="muted board-sample">成分 {role.data.total} · 抽样 {role.data.sample} · 日线有效 {role.data.price_coverage} · 资金有效 {role.data.flow_coverage}</div>
      <div className="role-grid">{Object.entries(role.data.roles).map(([label,stocks])=><div className="role" key={label}><h4>{label}</h4>
        {stocks.length?stocks.map(s=><div className="role-row" key={s.code}><button className="plain row-link" onClick={()=>select(s.code)}>{s.name}<small>{s.code.toUpperCase()}</small></button><div className="micro">涨跌 {percent(s.pct)} · 相对 {percent(s.relative)} · 近5日 {percent(s.return_5)}</div><div className="micro">成交额 {money(s.amount)} · 主力净额 {money(s.main_net,true)}</div><Explain score={s}/></div>):<Empty text="暂无符合条件的有效样本"/>}</div>)}</div>
      <details className="explain"><summary>成交额前 20 的成分股</summary>{role.data.members.map(s=><CapLine key={s.code} label={<button className="plain" onClick={()=>select(s.code)}>{s.name} {s.code.toUpperCase()}</button>} value={<>{percent(s.pct)} · {money(s.amount)} · {money(s.main_net,true)}</>}/>)}</details></>:
      <Empty text={role?.error||'正在核对板块成分、历史行情和个股资金'}/>}
    <Source p={role}/>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
