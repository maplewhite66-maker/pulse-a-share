import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyPlan,estimateBoardLots,fillError,mergeTradeReview,parseTradeReview,summarize} from '../src/trade-ledger.ts';

test('position estimate rounds down without inventing an affordable board lot',()=>{
  assert.deepEqual(estimateBoardLots('100000','12.55','25'),{lots:19,shares:1900,gross:23845});
  assert.deepEqual(estimateBoardLots('1255','12.55','100'),{lots:1,shares:100,gross:1255});
  assert.deepEqual(estimateBoardLots('1254.99','12.55','100'),{lots:0,shares:0,gross:0});
  assert.equal(estimateBoardLots('1255','0','100'),null);
  assert.equal(estimateBoardLots('1255','12.55','101'),null);
  assert.equal(estimateBoardLots('1e6','12.55','25'),null);
  assert.equal(estimateBoardLots('','12.55','25'),null);
});

test('average cost includes fees, sale only consumes proportional basis',()=>{
  const fill={side:'buy',price:10,quantity:200,fee:10,day:'2026-11-10'};
  const sell={side:'sell',price:11,quantity:100,fee:5,day:'2026-11-11'};
  const plan={...emptyPlan,fills:[fill]};
  assert.equal(fillError(sell,plan,'2026-11-11'),null);
  assert.deepEqual(summarize([fill,sell]),{shares:100,basis:1005,realized:90,fees:15,average:10.05});
  assert.match(fillError({...sell,day:fill.day},plan,'2026-11-11'),/可卖持仓/);
});

test('daily review preserves reported costs and skips repeated imports',()=>{
  const date='2026-11-11';
  const review=parseTradeReview(JSON.stringify([date,['sz000123','测试甲',12.345,300],['sh600123','测试乙',7.892,500]]),date);
  assert.ok(review);
  const before={'live:sz000123':{...emptyPlan,budget:12_000},'demo:sz000123':emptyPlan};
  const {plans,added}=mergeTradeReview(before,review);
  assert.equal(added,2);
  assert.equal(plans['live:sz000123'].budget,12_000);
  assert.equal(plans['demo:sz000123'],emptyPlan);
  assert.equal(summarize(plans['live:sz000123'].fills).average,12.345);
  assert.equal(summarize(plans['live:sh600123'].fills).shares,500);
  assert.equal(mergeTradeReview(plans,review).added,0);
  assert.match(fillError({side:'sell',day:date,price:11,quantity:100,fee:0},plans['live:sz000123'],date),/可卖持仓/);
});

test('daily review rejects invalid dates and values',()=>{
  const row=['sh600123','测试',11.123,500];
  assert.equal(parseTradeReview(JSON.stringify(['2026-02-30',row]),'2026-11-11'),null);
  assert.equal(parseTradeReview(JSON.stringify(['2026-11-12',row]),'2026-11-11'),null);
  assert.equal(parseTradeReview(JSON.stringify(['2026-11-11',[...row.slice(0,3),-500]]),'2026-11-11'),null);
});
