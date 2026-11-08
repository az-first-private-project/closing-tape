/**
 * 信号样本状态单测（Node 24 原生 TS 支持，不新增测试依赖）
 * 运行：node --test src/lib/sample.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizeSample, MIN_SAMPLE } from './sample.ts'

const stats = (partial) => ({
  sample_signals: 0,
  total_signals: 0,
  immature: 0,
  low_sample: true,
  ...partial,
})

test('缺少统计时明确提示不可用', () => {
  assert.equal(summarizeSample(null).tone, 'unavailable')
  assert.equal(summarizeSample(undefined).label, '样本统计不可用')
})

test('窗口内无信号与全部待成熟分开解释', () => {
  const empty = summarizeSample(stats({ total_signals: 0 }))
  assert.equal(empty.tone, 'empty')
  assert.equal(empty.label, '窗口内无信号')

  const pending = summarizeSample(
    stats({ total_signals: 211, immature: 211, sample_signals: 0 }),
  )
  assert.equal(pending.tone, 'pending')
  assert.equal(pending.detail, '0 条有效样本 · 211 条未成熟')
})

test('已成熟但不可成交时不冒充待成熟', () => {
  const result = summarizeSample(
    stats({ total_signals: 5, immature: 0, sample_signals: 0 }),
  )
  assert.equal(result.tone, 'empty')
  assert.equal(result.label, '无有效样本')
  assert.match(result.detail, /已成熟 5 条/)
})

test('低样本同时展示有效样本与成熟进度', () => {
  const result = summarizeSample(
    stats({
      total_signals: 23,
      immature: 18,
      sample_signals: 5,
      low_sample: true,
    }),
  )
  assert.equal(result.tone, 'low')
  assert.equal(result.label, '低样本')
  assert.equal(result.detail, '有效样本 5 · 已成熟 5/23')
})

test('样本达到阈值且后端未标记低样本时为充足', () => {
  const result = summarizeSample(
    stats({
      total_signals: 100,
      immature: 100 - MIN_SAMPLE,
      sample_signals: MIN_SAMPLE,
      low_sample: false,
    }),
  )
  assert.equal(result.tone, 'ok')
  assert.equal(result.label, '样本充足')
})
