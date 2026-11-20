import {fmt,money,timeText,useFeed,type Mode} from '../data';

type Seat={rank:number;seat:string;label:string;buy:number;sell:number;net:number;turnover_share:number|null};
type Disclosure={day:string;published_at:string;reason:string;buy:Seat[];sell:Seat[]};
type FlowKey='main_net'|'large_net'|'super_net'|'dark_net'|'northbound_net';
type Report={
  dominant:string; evidence_state:string; source:string; input_status:string|null;
  patterns:{key:string;label:string;score:number;evidence:string[]}[];
  today_report:Disclosure|null;latest_report:Disclosure|null;disclosed_days_20:number;seats_60_days:number;obi:number|null;
  flows:({day:string}&Record<FlowKey,number|null>)[];
  totals:Record<FlowKey,{days:number;amount:number|null}>;daily_flow_stale:boolean;
};
const labels: [FlowKey,string][]=[['main_net','主力净额'],['super_net','超大单'],['large_net','大单'],['dark_net','拆单估算'],['northbound_net','北向资金']];
const val=(n:number|null|undefined)=>n==null?'未取得':money(n,true);

export function CapitalProfilerPanel({mode,code,revision,openChart}:{mode:Mode;code:string;revision:number;openChart:(code:string)=>void}){
  const packet=useFeed<Report>('/api/stocks/'+encodeURIComponent(code)+'/capital-profile',mode,15000,revision);
  const data=packet?.data;
  const report=data?.today_report??data?.latest_report;
  const dominant=data?.patterns.find(p=>p.key===data.dominant);
  return <section className="panel research" id="capital-profile">
    <header className="research-head"><div><h2>个股资金画像</h2>
      <p>{code.toUpperCase()} · 席位与资金形态的规则吻合度。交易席位不能确认实际操盘者。</p></div>
      <button className="btn" onClick={()=>openChart(code)}>查看个股走势</button></header>
    {data?<><div className="screen-summary">
      <strong>{dominant?dominant.label+' · '+dominant.score+'/100':'证据不足，暂不分类'}</strong>
      <span>{data.today_report?'有当日披露':data.seats_60_days?'暂未取得当日披露，可查历史线索':'没有可核对的龙虎榜输入'}</span>
      <span>近 20 日披露 {data.disclosed_days_20} 日 · 近 60 日披露 {data.seats_60_days} 日</span>
      <span>五档 OBI {data.obi==null?'等待有效五档':fmt(data.obi,1)+'%'}</span>
    </div>
    <div className="v4-grid">{data.patterns.map(p=><article key={p.key}>
      <strong>{p.label} · {p.score}/100</strong><p>{p.evidence.join(' · ')||'暂无支持证据'}</p>
    </article>)}</div>
    {report?<><div className="research-foot">{data.today_report?'当日':'历史'}披露 · {report.day} · {timeText(report.published_at)} · {report.reason||'日榜涉及席位'}</div>
      <div className="table-wrap"><table className="research-table"><thead><tr><th>榜位</th><th>披露营业部 / 交易单元</th><th>标签</th><th>买入</th><th>卖出</th><th>净额</th><th>该方向占成交</th></tr></thead>
        <tbody>{(['buy','sell'] as const).flatMap(side=>report[side].map(s=><tr key={side+s.rank}>
          <td>{side==='buy'?'买':'卖'}{s.rank}</td><td>{s.seat}</td><td>{s.label}</td>
          <td>{val(s.buy)}</td><td>{val(s.sell)}</td><td>{val(s.net)}</td><td>{s.turnover_share==null?'分母未取得':fmt(s.turnover_share,2)+'%'}</td>
        </tr>))}</tbody></table></div></>:<p className="research-foot">{data.input_status??'暂无可核对的日榜席位明细。'}</p>}
    <div className="research-foot">最近五个交易日资金 · {data.daily_flow_stale?'日线采用现有归档':'按来源交易日展示'}{data.source?' · '+data.source:''}</div>
    {data.flows.length?<div className="table-wrap"><table className="research-table"><thead><tr><th>交易日</th>{labels.map(([key,label])=><th key={key}>{label}</th>)}</tr></thead>
      <tbody>{data.flows.map(row=><tr key={row.day}><td>{row.day}</td>{labels.map(([key])=><td key={key}>{val(row[key])}</td>)}</tr>)}
      <tr><th>有效数据合计 / 覆盖</th>{labels.map(([key])=><td key={key}>{val(data.totals[key].amount)} · {data.totals[key].days}/5 日</td>)}</tr></tbody>
    </table></div>:<p className="empty">等待个股日资金或授权资金来源数据。</p>}
    </>:<p className="empty">{packet?.error??'正在读取资金证据…'}</p>}
  </section>;
}
