import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTime, timestampPattern, timestampToSeconds } from './video-utils.js';

test('formatTime handles short, long, and invalid durations', () => {
  assert.equal(formatTime(0), '00:00');
  assert.equal(formatTime(125.9), '02:05');
  assert.equal(formatTime(3723), '01:02:03');
  assert.equal(formatTime(Number.NaN), '00:00');
  assert.equal(formatTime(-10), '00:00');
});

test('timestamp helpers parse grounded answer citations', () => {
  assert.equal(timestampToSeconds('03', '17'), 197);
  assert.deepEqual('Open [[00:04]] then [[02:10]]'.split(timestampPattern()), [
    'Open ',
    '[[00:04]]',
    ' then ',
    '[[02:10]]',
    '',
  ]);
});
