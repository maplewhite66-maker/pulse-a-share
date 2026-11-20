import test from 'node:test';
import assert from 'node:assert/strict';
import {sectorTreemap} from '../src/sector-treemap.ts';

test('active tiles fill the map in proportion to turnover, even for small sectors', () => {
  const rows = [30, 60, 9, 1, null, undefined, 0, NaN].map((amount, id) => ({amount, id}));
  const cells = sectorTreemap(rows);
  assert.deepEqual(cells.map(c => c.sector.id), [1, 0, 2, 3]);
  for (const cell of cells) {
    assert.ok(Math.abs(cell.width * cell.height / 100 - cell.sector.amount) < 1e-10);
    assert.ok(cell.left >= 0 && cell.top >= 0);
    assert.ok(cell.left + cell.width <= 100 && cell.top + cell.height <= 100);
  }
  assert.equal(cells.reduce((sum, cell) => sum + cell.width * cell.height, 0), 10_000);
  assert.deepEqual(sectorTreemap([{amount: null}]), []);
});
