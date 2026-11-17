import assert from 'node:assert/strict';
import {test} from 'node:test';
import {csvValue, externalUrl} from '../src/data.ts';

test('CSV exports neutralize formula prefixes and preserve negative numbers', () => {
  for (const value of ['=1+1', '  @SUM(A1)', '\nText', '\tText', '＝1+1', '\uFEFF+1']) {
    assert.ok(csvValue(value).startsWith('"\''), value);
  }
  assert.equal(csvValue(-45), '"-45"');
  assert.equal(csvValue('a,"b"'), '"a,""b"""');
});

test('market-source links accept credential-free HTTPS only', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,abc', 'http://127.0.0.1:8765/',
    '//source.example/', 'https://u:p@source.example/', 'https://source.example/\n']) {
    assert.equal(externalUrl(value), undefined, value);
  }
  assert.equal(externalUrl('https://source.example/a?q=42'), 'https://source.example/a?q=42');
});
