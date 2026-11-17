import assert from 'node:assert/strict';
import {test} from 'node:test';
import {newTrack,observe} from '../src/tracking.ts';

const quote=(second,price,is_stale=false)=>({name:'示例',market_time:`2026-11-10T14:30:${second}+08:00`,price,pct:1,is_stale});

test('baseline, crossing in both directions and duplicate quote protection',()=>{
  let track={...newTrack('sh600519'),upper:11,lower:9};
  track=observe(track,quote('01',10),true);
  assert.equal(track.baseline.price,10);
  track=observe(track,quote('02',11),true);
  assert.deepEqual(track.events.map(e=>e.direction),['upper']);
  assert.equal(observe(track,quote('02',12),true),track);
  assert.equal(observe(track,quote('01',8),true),track);
  track=observe(track,quote('03',12),true);
  track=observe(track,quote('04',9),true);
  assert.deepEqual(track.events.map(e=>e.direction),['upper','lower']);
  assert.equal(track.baseline.price,10);
});

test('stale data does not set a baseline and closed quotes never emit a crossing',()=>{
  let track={...newTrack('sh600519'),upper:11};
  assert.equal(observe(track,quote('01',10,true),true),track);
  track=observe(track,quote('02',10),false);
  track=observe(track,quote('03',11),false);
  assert.equal(track.samples.length,2);
  assert.deepEqual(track.events,[]);
});
