export type Position={code:string;name:string;shares:number;paid:number;day:string;entry:number};
export type Account={cash:number;positions:Position[];realized:number};
export type Quote={code:string;name:string;market_time:string;day:string;bid:number|null;ask:number|null;
  buy_shares:number;sell_shares:number;entry_allowed:boolean};
export const initialPaper:Account={cash:1_000_000,positions:[],realized:0};
const charge=(gross:number,sale=false)=>Math.round((Math.max(5,gross*.0005)+gross*(.00001+(sale ? .0005 : 0)))*100)/100;

export function paperFill(account:Account, quote:Quote, side:'buy'|'sell', day:string, open:boolean, now:number){
  const age=now-Date.parse(quote.market_time);
  if(!open||quote.day!==day||!Number.isFinite(age)||age<0||age>120_000)return {error:'仅使用交易时段 120 秒内的报价'};
  const position=account.positions.find(row=>row.code===quote.code);
  const price=side==='buy'?quote.ask:quote.bid;
  if(price==null||!Number.isFinite(price)||price<=0||(side==='buy'&&price>=50))return {error:'无有效对手价'};
  const adjusted=price*(side==='buy'?1.001:.999);
  if(side==='buy'){
    if(!quote.entry_allowed||position)return {error:'信号未通过风控或该标的已有模拟持仓'};
    const basis=account.cash+account.positions.reduce((s,row)=>s+row.paid,0);
    const budget=Math.min(basis*.20,account.cash);
    let shares=Math.min(Math.floor(budget/(100*adjusted))*100, Math.floor(quote.buy_shares/100)*100);
    if(!Number.isSafeInteger(shares))return {error:'模拟盘口深度无法核实'};
    while(shares>0&&shares*adjusted+charge(shares*adjusted)>budget)shares-=100;
    if(shares<100)return {error:'模拟资金不足一手'};
    const paid=shares*adjusted+charge(shares*adjusted);
    return {account:{...account,cash:account.cash-paid,
      positions:[...account.positions,{code:quote.code,name:quote.name,entry:adjusted,shares,paid,day}]}};
  }
  if(!position||position.day>=day)return {error:'该模拟持仓尚未到 T+1 可卖交易日'};
  if(!Number.isSafeInteger(quote.sell_shares)||position.shares>quote.sell_shares)return {error:'模拟买一深度不足以平仓'};
  const gross=position.shares*adjusted;
  const net=gross-charge(gross,true);
  return {account:{cash:account.cash+net,positions:account.positions.filter(row=>row.code!==quote.code),
    realized:account.realized+net-position.paid}};
}
