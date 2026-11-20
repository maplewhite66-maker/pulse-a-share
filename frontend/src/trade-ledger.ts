export type Side='buy'|'sell';
export type Fill={side:Side;price:number;quantity:number;fee:number;day:string;reportedName?:string};
export type Stage={side:Side;price:number;quantity:number;note:string};
export type TradePlan={budget:number;stages:Stage[];fills:Fill[]};

export const emptyPlan:TradePlan={budget:0,stages:[],fills:[]};
export const tradePlanStorage='trade-plans-v1';

export function estimateBoardLots(capital: string, price: string, percent: string) {
  // Parse the form's two-decimal inputs directly, keeping the budget calculation exact.
  const hundredths = (text: string) => {
    const match = /^(\d{0,12})(?:\.(\d{1,2}))?$/.exec(text.trim());
    if (!match || (!match[1] && !match[2])) return null;
    return BigInt(match[1] || '0') * 100n + BigInt((match[2] || '').padEnd(2, '0'));
  };
  const capitalCents = hundredths(capital);
  const priceCents = hundredths(price);
  const basisPoints = hundredths(percent);
  if (!capitalCents || !priceCents || !basisPoints || basisPoints > 10_000n) return null;
  const perLot = priceCents * 100n;
  const lots = capitalCents * basisPoints / (10_000n * perLot);
  return {lots: Number(lots), shares: Number(lots * 100n), gross: Number(lots * perLot) / 100};
}

export function summarize(fills:Fill[]){
  let shares=0,basis=0,realized=0,fees=0;
  for(const fill of fills){
    const gross=fill.price*fill.quantity;
    fees+=fill.fee;
    if(fill.side==='buy'){
      shares+=fill.quantity;
      basis+=gross+fill.fee;
    }else{
      const consumed=basis*fill.quantity/shares;
      shares-=fill.quantity;
      basis=shares?basis-consumed:0;
      realized+=gross-fill.fee-consumed;
    }
  }
  return {shares,basis,realized,fees,average:shares?basis/shares:null};
}

export function fillError(fill:Fill,plan:TradePlan,today:string){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(fill.day)||fill.day>today||fill.day<(plan.fills.at(-1)?.day??''))
    return '成交日期须按先后顺序登记，最晚为今天。';
  if(!Number.isFinite(fill.price)||fill.price<=0||!Number.isSafeInteger(fill.quantity)||fill.quantity<=0||
    !Number.isFinite(fill.fee)||fill.fee<0||!Number.isFinite(fill.price*fill.quantity+fill.fee))
    return '填写有效成交价、整数股数及非负费用。';
  if(fill.side==='sell'){
    const total=summarize(plan.fills).shares;
    const todayBuys=plan.fills.filter(row=>row.day===fill.day&&row.side==='buy').reduce((sum,row)=>sum+row.quantity,0);
    if(fill.quantity>total-todayBuys)return '卖出股数超过登记日可卖持仓，请先补录此前买入。';
  }
  return null;
}

export type TradeReview={day:string;rows:{code:string;fill:Fill}[]};

export function parseTradeReview(value:string,today:string):TradeReview|null{
  if(value.length>4_000)return null;
  try{
    const [day,...entries]:unknown[]=JSON.parse(value);
    if(typeof day!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(day)||day>today||
      new Date(day).toISOString().slice(0,10)!==day||!entries.length||entries.length>20)return null;
    const rows:TradeReview['rows']=[];
    for(const entry of entries){
      if(!Array.isArray(entry)||entry.length!==4)return null;
      const [code,name,price,quantity]=entry;
      if(typeof code!=='string'||!/^(sh|sz|bj)\d{6}$/.test(code)||/^(sh000|sz399)/.test(code)||
        typeof name!=='string'||!name.trim()||name.length>30)return null;
      const fill:Fill={side:'buy',price,quantity,fee:0,day,reportedName:name.trim()};
      if(fillError(fill,emptyPlan,today))return null;
      rows.push({code,fill});
    }
    return {day,rows};
  }catch{return null}
}

export function mergeTradeReview(previous:Record<string,TradePlan>,review:TradeReview){
  const plans={...previous};
  let added=0;
  for(const {code,fill} of review.rows){
    const key='live:'+code;
    const plan=plans[key]??emptyPlan;
    const present=plan.fills.some(row=>row.side==='buy'&&row.day===fill.day&&row.quantity===fill.quantity&&
      Math.abs(row.price+row.fee/row.quantity-fill.price)<0.0000001);
    if(present)continue;
    const fills=[...plan.fills,fill].sort((a,b)=>a.day.localeCompare(b.day));
    plans[key]={...plan,fills};
    added++;
  }
  return {plans,added};
}
