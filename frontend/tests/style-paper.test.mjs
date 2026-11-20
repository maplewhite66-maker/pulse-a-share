import test from 'node:test';
import assert from 'node:assert/strict';
import {initialPaper,paperFill} from '../src/style-paper.ts';

test('simulated order obeys depth, position budget, quote freshness and T+1',()=>{
  const first='2026-11-20', next='2026-11-23';
  const quote={code:'sh600123',name:'合成证券',day:first,market_time:first+'T14:50:00+08:00',
    bid:9.99,ask:10.01,buy_shares:900,sell_shares:1000,entry_allowed:true};
  const now=Date.parse(quote.market_time)+10_000;
  const firstFill=paperFill(initialPaper,quote,'buy',first,true,now).account;
  assert.equal(firstFill.positions[0].shares,900);
  assert.ok(firstFill.positions[0].paid<=200_000);
  assert.match(paperFill(firstFill,quote,'sell',first,true,now).error,/T\+1/);
  assert.match(paperFill(initialPaper,quote,'buy',first,true,now+120_000).error,/120/);
  assert.match(paperFill(initialPaper,quote,'buy',first,false,now).error,/交易时段/);
  const exit={...quote,day:next,market_time:next+'T09:45:00+08:00',bid:10.5};
  const marks=Date.parse(exit.market_time);
  assert.match(paperFill(firstFill,{...exit,sell_shares:100},'sell',next,true,marks).error,/深度/);
  const closed=paperFill(firstFill,exit,'sell',next,true,marks).account;
  assert.equal(closed.positions.length,0);
  assert.ok(closed.realized>0);
  assert.equal(initialPaper.cash,1_000_000);
});
