/**
 * 命令面板纯逻辑单测（Node 24 原生 TS 支持，不新增测试依赖）
 * 运行：node --test src/lib/palette.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  loadPaletteRecent,
  mergeRecent,
  PALETTE_RECENT_LIMIT,
  splitHighlight,
} from './palette.ts'

test('splitHighlight 只做子串切分并保留原文大小写', () => {
  assert.deepEqual(splitHighlight('贵州茅台', '贵州'), [
    { text: '贵州', match: true },
    { text: '茅台', match: false },
  ])
  assert.deepEqual(splitHighlight('AlgoCenter', 'center'), [
    { text: 'Algo', match: false },
    { text: 'Center', match: true },
  ])
})

test('splitHighlight 支持多次命中与空查询', () => {
  assert.deepEqual(splitHighlight('a-b-a', 'a'), [
    { text: 'a', match: true },
    { text: '-b-', match: false },
    { text: 'a', match: true },
  ])
  assert.deepEqual(splitHighlight('任意文本', '   '), [{ text: '任意文本', match: false }])
  assert.deepEqual(splitHighlight('', 'x'), [])
})

test('注入形态的查询词只返回纯文本片段', () => {
  const parts = splitHighlight('<img src=x onerror=alert(1)>', '<img')
  assert.deepEqual(parts, [
    { text: '<img', match: true },
    { text: ' src=x onerror=alert(1)>', match: false },
  ])
  assert.equal(parts.some((part) => part.text.includes('dangerously')), false)
})

test('mergeRecent 去重且把新记录置顶', () => {
  const first = { kind: 'page', label: '首页', path: '/' }
  const second = { kind: 'stock', label: '贵州茅台', path: '/stocks/600519' }
  const merged = mergeRecent([first, second], second)
  assert.deepEqual(merged.map((entry) => entry.path), ['/stocks/600519', '/'])
})

test('mergeRecent 上限为 10 条', () => {
  const entries = Array.from({ length: 12 }, (_, index) => ({
    kind: 'page',
    label: `页面 ${index}`,
    path: `/page-${index}`,
  }))
  const merged = mergeRecent(entries, { kind: 'algo', label: '策略', path: '/algo-center?algo=b1' })
  assert.equal(merged.length, PALETTE_RECENT_LIMIT)
  assert.equal(merged[0].path, '/algo-center?algo=b1')
})

test('loadPaletteRecent 容错非法 JSON 与无效条目', () => {
  assert.deepEqual(loadPaletteRecent({ getItem: () => '{not json' }), [])
  assert.deepEqual(loadPaletteRecent({ getItem: () => null }), [])
  const raw = JSON.stringify([
    { kind: 'page', label: '首页', path: '/' },
    { kind: 'unknown', label: '坏数据', path: '/bad' },
    { kind: 'stock', label: '', path: '/stocks/1' },
    { kind: 'page', label: '首页副本', path: '/' },
    { kind: 'algo', label: 'B1', path: '/algo-center?algo=b1', sublabel: 'b1 · 布林' },
  ])
  assert.deepEqual(loadPaletteRecent({ getItem: () => raw }), [
    { kind: 'page', label: '首页', path: '/' },
    { kind: 'algo', label: 'B1', path: '/algo-center?algo=b1', sublabel: 'b1 · 布林' },
  ])
})

test('loadPaletteRecent 读存储抛错时降级为空数组', () => {
  assert.deepEqual(loadPaletteRecent({ getItem: () => { throw new Error('blocked') } }), [])
})
