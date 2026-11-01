/**
 * Sparkline 几何单测（Node 24 原生 TS 支持，不新增测试依赖）
 * 运行：node --test src/lib/sparkline.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildSparklineGeometry,
  sampleSparklineValues,
  SPARKLINE_MAX_POINTS,
} from './sparkline.ts'

const near = (left, right, epsilon = 1e-6) => Math.abs(left - right) <= epsilon

test('null 断点分段，缺失点计数正确', () => {
  const geometry = buildSparklineGeometry({
    values: [1, null, 2, 3],
    width: 100,
    height: 20,
  })
  assert.equal(geometry.points, 4)
  assert.equal(geometry.validCount, 3)
  assert.equal(geometry.missingCount, 1)
  assert.deepEqual(geometry.segments.map((segment) => segment.length), [1, 2])
  assert.equal(geometry.first, 1)
  assert.equal(geometry.last, 3)
  assert.equal(geometry.flat, false)
  assert.equal(geometry.dataInsufficient, false)
})

test('空数组渲染全宽平线，不抛错', () => {
  const geometry = buildSparklineGeometry({ values: [], width: 90, height: 24 })
  assert.equal(geometry.points, 0)
  assert.equal(geometry.validCount, 0)
  assert.equal(geometry.flat, true)
  assert.equal(geometry.dataInsufficient, true)
  assert.equal(geometry.min, null)
  assert.equal(geometry.segments.length, 1)
  assert.deepEqual(geometry.segments[0], [
    { x: 0, y: 12 },
    { x: 90, y: 12 },
  ])
})

test('单点渲染全宽平线并标记数据不足', () => {
  const geometry = buildSparklineGeometry({ values: [5], width: 100, height: 30 })
  assert.equal(geometry.points, 1)
  assert.equal(geometry.validCount, 1)
  assert.equal(geometry.flat, true)
  assert.equal(geometry.dataInsufficient, true)
  assert.equal(geometry.first, 5)
  assert.equal(geometry.last, 5)
  assert.deepEqual(geometry.segments, [
    [
      { x: 0, y: 15 },
      { x: 100, y: 15 },
    ],
  ])
})

test('全 null 按缺失计数渲染中线', () => {
  const geometry = buildSparklineGeometry({ values: [null, null], width: 80, height: 20 })
  assert.equal(geometry.validCount, 0)
  assert.equal(geometry.missingCount, 2)
  assert.equal(geometry.flat, true)
  assert.equal(geometry.segments.length, 1)
  assert.ok(geometry.segments[0].every((point) => point.y === 10))
})

test('全等值渲染为水平线且不产生 0 除', () => {
  const geometry = buildSparklineGeometry({ values: [3, 3, 3], width: 100, height: 24 })
  assert.equal(geometry.flat, true)
  assert.equal(geometry.dataInsufficient, false)
  assert.equal(geometry.min, 3)
  assert.equal(geometry.max, 3)
  const ys = geometry.segments[0].map((point) => point.y)
  assert.ok(ys.every((value) => near(value, ys[0])))
  assert.ok(Number.isFinite(ys[0]))
})

test('超过 days 只保留最后 N 点', () => {
  const values = Array.from({ length: 50 }, (_, index) => index + 1)
  const sampled = sampleSparklineValues(values, 10)
  assert.equal(sampled.length, 10)
  assert.equal(sampled[0], 41)
  assert.equal(sampled[sampled.length - 1], 50)
})

test('超过 200 点等距降采样并保留首尾', () => {
  const values = Array.from({ length: 1000 }, (_, index) => index)
  const geometry = buildSparklineGeometry({ values, days: 1000, width: 200, height: 40 })
  assert.equal(geometry.points, SPARKLINE_MAX_POINTS)
  assert.equal(geometry.first, 0)
  assert.equal(geometry.last, 999)
  assert.equal(geometry.segments[0].length, SPARKLINE_MAX_POINTS)
  assert.equal(geometry.segments[0][0].x, 0)
  assert.equal(geometry.segments[0][geometry.segments[0].length - 1].x, 200)
})

test('显式 domain 固定纵轴，首末点贴合可用高度', () => {
  const geometry = buildSparklineGeometry({
    values: [0, 50, 100],
    domain: [0, 100],
    width: 100,
    height: 24,
    padding: 2,
  })
  assert.deepEqual(
    geometry.segments[0].map((point) => point.y),
    [22, 12, 2],
  )
})

test('自适应纵轴留出上下余量', () => {
  const geometry = buildSparklineGeometry({ values: [1, 2, 3], width: 100, height: 32, padding: 2 })
  const ys = geometry.segments[0].map((point) => point.y)
  assert.ok(ys[0] > 2 && ys[0] < 30)
  assert.ok(ys[2] > 2 && ys[2] < 30)
  assert.ok(ys[0] > ys[2])
})

test('baseline 越界时不绘制，界内映射到正确纵坐标', () => {
  const inside = buildSparklineGeometry({
    values: [0, 100],
    domain: [0, 100],
    baseline: 50,
    height: 24,
    padding: 2,
  })
  assert.equal(inside.baselineY, 12)
  const outside = buildSparklineGeometry({
    values: [0, 100],
    domain: [0, 100],
    baseline: 150,
    height: 24,
    padding: 2,
  })
  assert.equal(outside.baselineY, null)
})

test('非有限值按缺数据处理', () => {
  const geometry = buildSparklineGeometry({
    values: [Number.NaN, Number.POSITIVE_INFINITY, 2],
    width: 100,
    height: 20,
  })
  assert.equal(geometry.validCount, 1)
  assert.equal(geometry.missingCount, 2)
  assert.equal(geometry.dataInsufficient, true)
  assert.ok(geometry.segments.flat().every((point) => Number.isFinite(point.y)))
})

test('横坐标等距分布', () => {
  const geometry = buildSparklineGeometry({ values: [1, 2, 3], width: 100, height: 20 })
  assert.deepEqual(
    geometry.segments[0].map((point) => point.x),
    [0, 50, 100],
  )
})
