import test from 'node:test';
import assert from 'node:assert/strict';
import {sortGroundVolume} from '../src/ground-volume-sort.ts';

test('ground-volume sorting preserves official ranks, breaks ties by rank, and leaves the input intact', () => {
  const rows = [
    {rank:2, score:70, win_rate:80, mean_return:2, max_drawdown:6},
    {rank:1, score:80, win_rate:70, mean_return:0, max_drawdown:6},
    {rank:3, score:60, win_rate:80, mean_return:-1, max_drawdown:3},
    {rank:4, score:null, win_rate:null, mean_return:null, max_drawdown:null},
  ];
  const ranks = (key) => sortGroundVolume(rows,key).map(r=>r.rank);
  assert.deepEqual(ranks('score'),[1,2,3,4]);
  assert.deepEqual(ranks('win_rate'),[2,3,1,4]);
  assert.deepEqual(ranks('mean_return'),[2,1,3,4]);
  assert.deepEqual(ranks('max_drawdown'),[3,1,2,4]);
  assert.deepEqual(rows.map(row=>row.rank),[2,1,3,4]);
  assert.deepEqual(sortGroundVolume([],'score'),[]);
});
