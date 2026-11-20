import {fmt,timeText,useFeed,type Mode} from '../data';
type Row={rank:number;code:string;name:string;price:number;market_time:string;board:string;board_rank:number;tracks:string[];score:number;
  capital_evidence:string;ground_rank:number|null;max_position_pct:number;s1:number;stop:number;take_pct:number;stop_pct:number};
type Packet={status:string;items:Row[];message:string|null;as_of?:string;sector_market_time?:string;ground_slot?:string|null};
export function PulseV4Strategy({mode,revision,openChart,openProfile}:{mode:Mode;revision:number;
  openChart:(code:string)=>void;openProfile:(code:string)=>void}){
  const feed=useFeed<Packet>('/api/strategy/pulse-v4',mode,5000,revision);
  const data=feed?.data;
  return <section id="pulse-v4" className="panel research">
    <header className="research-head"><div><h2>PULSE 4.0 · 四维融合</h2>
      <p>行业资金前五、资金形态排雷、双轨入场条件和硬性仓位约束。</p></div>
      <span className="badge">{data?.status==='ready'?'已核验':'等待有效信号'}</span></header>
    <div className="screen-summary"><strong>进攻 14:50–14:57 · 防守 地量严选</strong>
      <span>板块时标 {data?.sector_market_time?timeText(data.sector_market_time):'待核对'}</span>
      <span>地量快照 {data?.ground_slot??'未取得当日档案'}</span></div>
    {data?.items.length?<div className="table-wrap"><table className="research-table"><thead><tr><th>排名 / 标的</th><th>板块</th><th>轨道</th><th>资金证据</th><th>得分</th><th>规则价位</th><th>仓位上限</th></tr></thead>
      <tbody>{data.items.map(r=><tr key={r.code}><td>{r.rank} · <button className="plain" onClick={()=>openChart(r.code)}>{r.name}</button><small className="micro"> {r.code.toUpperCase()}</small></td>
        <td>#{r.board_rank} {r.board}</td><td>{r.tracks.join(' / ')}{r.ground_rank?' · 地量 #'+r.ground_rank:''}</td>
        <td><button className="plain" onClick={()=>openProfile(r.code)}>{r.capital_evidence}</button></td><td>{fmt(r.score,2)}</td>
        <td>现价 {fmt(r.price)} · S1 {fmt(r.s1)} · 止损 {fmt(r.stop)}</td><td>{r.max_position_pct}%</td></tr>)}</tbody>
    </table></div>:<p className="empty">{feed?.error??data?.message??(data?.status==='live_only'?'请切换真实模式':'当前无满足全部条件的标的。')}</p>}
    <p className="research-foot">超时、缺证据或触发风控的标的不会入榜。S1、止损和仓位为模型数值，页面不连接券商。</p>
  </section>;
}
