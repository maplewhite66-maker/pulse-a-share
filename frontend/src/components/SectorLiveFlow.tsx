import {useEffect, useRef, useState} from 'react';
import {init, use, type EChartsCoreOption} from 'echarts/core';
import {BarChart, LineChart, TreemapChart} from 'echarts/charts';
import {GridComponent, LegendComponent, MarkPointComponent, TooltipComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import {fmt, money, percent, timeText, tone, useFeed, type Mode} from '../data';

use([BarChart, LineChart, TreemapChart, GridComponent, LegendComponent, MarkPointComponent, TooltipComponent, CanvasRenderer]);
const red='#8e394a', green='#166d58', gray='#314656';
const ink='#cbd6e2', axis={axisLabel:{color:ink,fontSize:10}};
const palette=['#53dfbf','#9da9ff','#fbbf65','#8dc8ff','#cda1ff','#ff7e8d','#eeb6c0','#bbc4d1'];

type Board = {code:string; name:string; pct:number|null; amount:number|null; amount_share:number|null;
  main_net:number; super_net:number|null; large_net:number|null; leader:string; leader_code:string; market_time:string|null};
type Point={t:string;net:number;pct:number|null};
type Series={code:string;name:string;points:Point[];marks:(Point & {label:string})[]};
type Member={code:string;name:string;price:number|null;pct:number|null;main_net:number;market_time:string};
type Data={status:string;items:Board[];series:Series[];covered:number; total?:number; day?:string;market_time?:string|null;source?:string;
  drilldown?:{status:string;items:Member[]}};
const stateLabel:Record<string,string>={fresh:'时标已校验',unverified:'上游时标待核对',stale:'上次有效快照',
  archive:'历史快照',waiting:'等待可用数据',live_only:'真实模式可用'};
const mv=(n:number|null|undefined, sign=false)=>n==null?'未取得':money(n,sign);

function Chart({option,label,onSelect}:{option:EChartsCoreOption;label:string;onSelect?:(board:string)=>void}){
  const canvas=useRef<HTMLDivElement>(null);
  const chart=useRef<ReturnType<typeof init>|null>(null);
  const click=useRef(onSelect);
  useEffect(()=>{click.current=onSelect},[onSelect]);
  useEffect(()=>{
    if(!canvas.current)return;
    const node=canvas.current, instance=init(node);
    chart.current=instance;
    const resize=new ResizeObserver(()=>instance.resize());
    resize.observe(node);
    instance.on('click', (event:{data?:unknown})=>{
      const data=event.data;
      if(data && typeof data==='object' && 'boardCode' in data && typeof data.boardCode==='string'){
        click.current?.(data.boardCode);
      }
    });
    return ()=>{resize.disconnect();instance.dispose();chart.current=null};
  },[]);
  useEffect(()=>{chart.current?.setOption(option,{notMerge:true})},[option]);
  return <div className="stream-chart" ref={canvas} role="img" aria-label={label}/>;
}

const chartTip={renderMode:'richText',backgroundColor:'#162230',borderColor:'#49687c',textStyle:{color:ink},confine:true};
const unit=(value:number)=>Number((value/1e8).toFixed(4));
const fill=(pct:number|null)=>pct==null||pct===0?gray:pct>0?red:green;
function mapChart(boards:Board[], selected:string):EChartsCoreOption {
  return {animation:false, tooltip:{...chartTip,formatter:'{b}'},series:[{
    type:'treemap',roam:false,nodeClick:false,breadcrumb:{show:false},visibleMin:0,
    top:0,bottom:0,left:0,right:0,sort:'desc',upperLabel:{show:false},
    label:{show:true,color:'#fff',overflow:'truncate'},
    data:boards.filter(r=>r.amount!=null&&r.amount>0).map(r=>({
      boardCode:r.code,name:`${r.name}\n${r.main_net>0?'流入':r.main_net<0?'流出':'持平'} ${mv(r.main_net,true)}`,
      value:r.amount,itemStyle:{color:fill(r.pct),borderWidth:r.code===selected?3:2,
        borderColor:r.main_net>0?'#ff9bab':r.main_net<0?'#6affd1':'#687c91',gapWidth:0},
    })),
  }]};
}
function linesChart(series:Series[]):EChartsCoreOption{
  const stamps=[...new Set(series.flatMap(s=>s.points.map(p=>p.t)))].sort();
  return {animation:false,color:palette,tooltip:{...chartTip,trigger:'axis'},legend:{top:0,type:'scroll',textStyle:{color:ink}},
    grid:{left:10,right:24,top:50,bottom:26,containLabel:true},
    xAxis:{...axis,type:'category',data:stamps.map(t=>t.slice(11,16))},yAxis:{...axis,type:'value',name:'亿元',nameTextStyle:{color:ink}},
    series:series.map(s=>{
      const values=new Map(s.points.map(p=>[p.t,unit(p.net)]));
      return {name:s.name,type:'line',showSymbol:false,connectNulls:false,data:stamps.map(t=>values.get(t)??null),
        markPoint:{symbolSize:24,label:{show:false},data:s.marks.map(m=>({name:m.label,coord:[m.t.slice(11,16),unit(m.net)],value:m.label}))}};
    }),
  };
}
function barsChart(boards:Board[]):EChartsCoreOption{
  const rows=[...boards.filter(r=>r.main_net>0).slice(0,5),
    ...boards.filter(r=>r.main_net<0).slice(-3)].reverse();
  return {animation:false,tooltip:{...chartTip,formatter:'{b}: {c} 亿'},
    grid:{left:5,right:24,top:12,bottom:18,containLabel:true},
    xAxis:{...axis,type:'value'},yAxis:{...axis,type:'category',axisLabel:{color:ink,fontSize:10,width:90,overflow:'truncate'},data:rows.map(r=>r.name)},
    series:[{type:'bar',data:rows.map(r=>({boardCode:r.code,value:unit(r.main_net),itemStyle:{color:r.main_net>0?red:green}}))}]};
}

export function SectorLiveFlow({mode,revision,openStock,embedded=false}:{mode:Mode;revision:number;
  openStock:(code:string)=>void;embedded?:boolean}){
  const [kind,setKind]=useState<'industry'|'concept'>('industry');
  const [selected,setSelected]=useState('');
  const [view,setView]=useState<'map'|'lines'|'bars'>('map');
  const path='/api/sectors/live-flow?kind='+kind;
  const feed=useFeed<Data>(path,mode,5000,revision);
  const memberFeed=useFeed<Data>(selected?path+'&board='+encodeURIComponent(selected):null,mode,25_000,revision);
  const result=feed?.data, boards=result?.items??[], series=result?.series??[];
  const board=boards.find(r=>r.code===selected);
  const members=memberFeed?.data?.drilldown?.items??[];
  const marks=series.flatMap(s=>s.marks.map(m=>({board:s.name,...m}))).sort((a,b)=>b.t.localeCompare(a.t)).slice(0,8);
  return <section id={embedded?undefined:'sector-live'} className={embedded?'sector-stream-inline':'panel research'}>
    {!embedded&&<header className="research-head"><div><h2>板块资金动态</h2>
      <p>板块净额对比与分时轮动。图中资金净额是数据源的估算口径。</p></div></header>}
    <div className="research-bar">
      <label>板块分类<select value={kind} onChange={e=>{setKind(e.target.value as typeof kind);setSelected('')}}>
        <option value="industry">行业</option><option value="concept">概念</option></select></label>
      <div className="tabs" role="group" aria-label="资金视图">
        {([['map','成交额热力图'],['lines','分时轮动'],['bars','净额对比']] as const).map(([key,label])=><button key={key}
          className={view===key?'active':''} aria-pressed={view===key} onClick={()=>setView(key)}>{label}</button>)}
      </div>
      <span className="badge" role="status">{feed?.error?'接口暂不可用':stateLabel[result?.status??'waiting']??'等待可用数据'}</span>
    </div>
    {boards.length?<><div className="research-foot">{result?.source??feed?.source??'来源待核对'} · {result?.day??'日期待核对'} · {result?.market_time?timeText(result.market_time):'上游未给出可核对时标'} · 覆盖 {result?.covered} / {result?.total||'未公布'} 个板块</div>
      {view==='map'&&<><p className="research-foot">面积按榜内成交额计算。红涨绿跌，边框区分资金方向。</p>
        {boards.some(r=>r.amount!=null&&r.amount>0)?<Chart option={mapChart(boards,selected)} label="成交额加权板块热力图" onSelect={setSelected}/>:<p className="empty">成交额缺失，可通过板块列表下钻。</p>}</>}
      {view==='lines'&&<>{series.some(s=>s.points.length>1)?<Chart option={linesChart(series)} label="板块分时资金曲线"/>:
        <p className="empty">等待至少两次已校验的分时资金记录。</p>}
        {!!marks.length&&<div className="research-foot">{marks.map((m,i)=><div key={i}>{timeText(m.t)} · {m.board} · {m.label}</div>)}</div>}</>}
      {view==='bars'&&<><Chart option={barsChart(boards)} label="板块资金净额对比" onSelect={setSelected}/>
        <p className="research-foot">按板块分别比较净流入与净流出。公开净额无法追踪同一笔资金从哪个板块转入。</p></>}
      <div className="research-bar"><label>查看板块成分<select value={board?selected:''} onChange={e=>setSelected(e.target.value)}>
        <option value="">选择板块</option>{boards.map(r=><option key={r.code} value={r.code}>{r.name}</option>)}</select></label>
        {board&&<><span>主力 {mv(board.main_net,true)} · 超大单 {mv(board.super_net,true)} · 大单 {mv(board.large_net,true)}</span>
          <span>当日涨跌 {board.pct==null?'未取得':percent(board.pct)} · 成交额占榜 {board.amount_share==null?'未取得':fmt(board.amount_share,1)+'%'}</span>
          {board.leader_code&&/^(sh|sz|bj)?\d{6}$/.test(board.leader_code)&&board.leader
            ?<button className="btn" onClick={()=>openStock(board.leader_code)}>领涨 · {board.leader}</button>:<span>领涨 · {board.leader||'待确认'}</span>}</>}
      </div>
      {board&&(members.length?<div className="table-wrap"><table className="research-table"><thead><tr><th>净流入前 10 成分</th><th>价格</th><th>涨跌</th><th>主力净额</th><th>行情时点</th></tr></thead>
        <tbody>{members.map(r=><tr key={r.code}><td><button className="plain" onClick={()=>openStock(r.code)}>{r.name||r.code}</button></td>
          <td>{r.price==null?'未取得':fmt(r.price)}</td><td className={tone(r.pct)}>{r.pct==null?'未取得':percent(r.pct)}</td>
          <td>{mv(r.main_net,true)}</td><td>{timeText(r.market_time)}</td></tr>)}</tbody></table></div>:
        <p className="empty">暂未取得可核对的成分股资金数据。</p>)}
    </>:<p className="empty">{feed?.error??'等待盘中资金快照，休市后可查阅已归档记录。'}</p>}
  </section>;
}
