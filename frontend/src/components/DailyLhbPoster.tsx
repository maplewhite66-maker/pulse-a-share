import {useState} from 'react';
import {externalUrl,fmt,timeText,useFeed,type Mode} from '../data';

type Listing={code:string;name:string;scope:'daily'|'multi'|'three';cause:string;trade_id:string;net_cents:number;seats:string[]};
type Report={day:string;generated_at:string;revision:string;source:string;source_url:string;buy_net_cents:number;sell_net_cents:number;
  listed_events:number;matched_events:number;unmatched_events:number;daily_stocks:number;multi_stocks:number;
  groups:{label:string;items:Listing[]}[]};
type ResponseData={report:Report|null;days:string[];day:string;enabled:boolean;running:boolean;message:string|null};
const amount=(cents:number)=>{
  const value=Math.abs(cents)/100;
  const base=value>=1e8?fmt(value/1e8,2)+'亿':value>=1e4?fmt(value/1e4,0)+'万':fmt(value,0)+'元';
  return (cents>0?'+':cents<0?'−':'')+base;
};

async function rasterize(src:string){
  const image=new Image();
  image.src=src;
  await image.decode();
  const canvas=document.createElement('canvas');
  canvas.width=image.naturalWidth;
  canvas.height=image.naturalHeight;
  const context=canvas.getContext('2d');
  if(!context||!canvas.width||!canvas.height)throw new Error('无法生成图片');
  context.drawImage(image,0,0);
  return await new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('无法导出图片')),'image/png'));
}

export function DailyLhbPoster({mode,revision,openChart}:{mode:Mode;revision:number;openChart:(code:string)=>void}){
  const [day,setDay]=useState('');
  const [feedback,setFeedback]=useState('');
  const [exporting,setExporting]=useState(false);
  const [showDetails,setShowDetails]=useState(false);
  const latest=useFeed<ResponseData>('/api/reports/daily-lhb',mode,60_000,revision);
  const archived=useFeed<ResponseData>(day?'/api/reports/daily-lhb?day='+encodeURIComponent(day):null,mode,0,revision);
  const packet=day?archived:latest;
  const data=packet?.data, report=data?.report;
  const src=report?'/api/reports/daily-lhb/'+report.day+'.svg?rv='+encodeURIComponent(report.revision):'';
  async function downloadPng(){
    if(!report||!src||exporting)return;
    setExporting(true);setFeedback('');
    try{
      const url=URL.createObjectURL(await rasterize(src));
      const link=document.createElement('a');
      link.href=url;link.download='PULSE-游资龙虎榜-'+report.day+'.png';link.click();
      window.setTimeout(()=>URL.revokeObjectURL(url),10000);
    }catch{
      setFeedback('PNG 导出未成功，可直接下载 SVG。');
    }finally{setExporting(false)}
  }
  return <section className="lhb-review" aria-labelledby="daily-lhb-heading">
    <header className="research-head"><div><h3 id="daily-lhb-heading">每日游资龙虎榜</h3>
      <p>交易日 17:00 起生成，单日与累计披露分开统计。</p></div>
      <span className="badge">{data?.running?'汇总中':report?(report.day===data?.day?'当日已归档':'历史归档'):'等待披露'}</span></header>
    <div className="research-bar">
      <label>龙虎榜日期<select value={day} onChange={e=>{setDay(e.target.value);setFeedback('');setShowDetails(false)}}>
        <option value="">最新归档</option>{(latest?.data?.days??[]).map(d=><option value={d} key={d}>{d}</option>)}
      </select></label>
      {report&&<><button type="button" className="btn research-primary" disabled={exporting} onClick={()=>void downloadPng()}>{exporting?'正在导出…':'下载 PNG'}</button>
        <a className="btn" href={src} download={'PULSE-游资龙虎榜-'+report.day+'.svg'}>下载 SVG</a>
        <a href={externalUrl(report.source_url)} target="_blank" rel="noreferrer">披露来源 ↗</a></>}
    </div>
    {feedback&&<p className="research-foot" role="status">{feedback}</p>}
    {!report?<p className="empty" role="status">{packet?.error??data?.message??(data?(day?'该日期暂无龙虎榜归档。':data.enabled?'暂无可核对的龙虎榜归档。':'当前环境的龙虎榜定时任务未开启。'):'正在读取龙虎榜…')}</p>:<>
      {data?.message&&<p className="research-foot" role="status">{data.message}</p>}
      <div className="research-foot">{report.day} · {timeText(report.generated_at)} · 披露匹配 {report.matched_events}/{report.listed_events}
        {report.matched_events<report.listed_events||report.unmatched_events>0?' · 存在未匹配披露':''} · {report.source}
      </div>
      <figure className="lhb-poster"><img src={src} alt={report.day+' 游资龙虎榜席位净额海报'}/></figure>
      <div className="research-bar"><button type="button" className="btn" aria-expanded={showDetails} onClick={()=>setShowDetails(v=>!v)}>
        {showDetails?'收起席位明细':'展开全部席位明细'} · {report.groups.length} 组</button></div>
      {showDetails&&<div className="table-wrap research-table"><table><thead>
        <tr><th>披露席位 / 分组</th><th>证券</th><th>披露期间</th><th>净额</th><th>上榜原因与原始名称</th></tr></thead>
        <tbody>{report.groups.flatMap(g=>g.items.map(r=><tr key={g.label+r.trade_id+r.code}>
          <td>{g.label}</td><td><button type="button" className="plain row-link" onClick={()=>openChart(r.code)}>{r.name}<small>{r.code.toUpperCase()}</small></button></td>
          <td>{r.scope==='three'?'三日':r.scope==='multi'?'累计':'单日'}</td>
          <td className={r.net_cents>0?'rise':'fall'}>{amount(r.net_cents)}</td>
          <td><details><summary>{r.cause}</summary>{r.seats.map(s=><div key={s}>{s}</div>)}</details></td>
        </tr>))}</tbody>
      </table></div>}
    </>}
    <p className="research-foot">名称取自交易公开信息，简称是营业部分组。净额只反映公开席位，不能用来确认个人身份。</p>
  </section>;
}
