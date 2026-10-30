/**
 * squarify 纯函数单测（Node 24 原生 TS 支持，不新增测试依赖）
 * 运行：node --test src/lib/treemap.test.mjs
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { squarify } from './treemap.ts'

const close = (left, right, epsilon = 1e-6) => Math.abs(left - right) <= epsilon

function area(rect) {
  return Math.max(0, rect.w) * Math.max(0, rect.h)
}

function overlapArea(a, b) {
  const width = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const height = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  return width > 0 && height > 0 ? width * height : 0
}

test('面积守恒：所有方块面积之和 = 盒子面积，且按权重分配', () => {
  const box = { x: 0, y: 0, w: 640, h: 360 }
  const items = [
    { id: 'a', value: 50 },
    { id: 'b', value: 25 },
    { id: 'c', value: 15 },
    { id: 'd', value: 10 },
  ]
  const rects = squarify(items, box)
  assert.equal(rects.length, items.length)

  const rectArea = rects.reduce((sum, rect) => sum + area(rect), 0)
  assert.ok(close(rectArea, box.w * box.h, 1e-3), `总面积 ${rectArea}`)

  for (const item of items) {
    const rect = rects.find((candidate) => candidate.id === item.id)
    assert.ok(rect, `缺少 ${item.id}`)
    assert.ok(close(area(rect), (item.value / 100) * box.w * box.h, 1e-3))
  }
})

test('不重叠且全部落在盒子内', () => {
  const box = { x: 12, y: 8, w: 500, h: 300 }
  const items = Array.from({ length: 37 }, (_, index) => ({
    id: `s${index}`,
    value: (index + 1) ** 1.3,
  }))
  const rects = squarify(items, box)
  assert.equal(rects.length, items.length)

  for (let i = 0; i < rects.length; i += 1) {
    const rect = rects[i]
    assert.ok(rect.x >= box.x - 1e-6 && rect.y >= box.y - 1e-6)
    assert.ok(rect.x + rect.w <= box.x + box.w + 1e-5)
    assert.ok(rect.y + rect.h <= box.y + box.h + 1e-5)
    for (let j = i + 1; j < rects.length; j += 1) {
      assert.ok(overlapArea(rect, rects[j]) <= 1e-6, `${rect.id} 与 ${rects[j].id} 重叠`)
    }
  }
})

test('单元素铺满盒子', () => {
  const box = { x: 5, y: 7, w: 300, h: 120 }
  const [rect] = squarify([{ id: 'only', value: 42 }], box)
  assert.deepEqual(
    { x: rect.x, y: rect.y, w: rect.w, h: rect.h },
    { x: 5, y: 7, w: 300, h: 120 },
  )
})

test('全等值：生成等面积方块', () => {
  const box = { x: 0, y: 0, w: 400, h: 400 }
  const items = Array.from({ length: 12 }, (_, index) => ({ id: `e${index}`, value: 7 }))
  const rects = squarify(items, box)
  const expected = (400 * 400) / 12
  assert.equal(rects.length, 12)
  for (const rect of rects) {
    assert.ok(close(area(rect), expected, 1e-3), `${rect.id} 面积 ${area(rect)}`)
  }
})

test('过滤 0 / 负值 / 非有限值；无有效输入返回空', () => {
  const box = { x: 0, y: 0, w: 100, h: 100 }
  const rects = squarify([
    { id: 'zero', value: 0 },
    { id: 'negative', value: -3 },
    { id: 'nan', value: Number.NaN },
    { id: 'inf', value: Number.POSITIVE_INFINITY },
    { id: 'ok', value: 5 },
  ], box)
  assert.deepEqual(rects.map((rect) => rect.id), ['ok'])

  assert.deepEqual(squarify([{ id: 'zero', value: 0 }], box), [])
  assert.deepEqual(squarify([], box), [])
})

test('退化盒子（宽或高为 0）返回空', () => {
  assert.deepEqual(squarify([{ id: 'a', value: 1 }], { x: 0, y: 0, w: 0, h: 100 }), [])
  assert.deepEqual(squarify([{ id: 'a', value: 1 }], { x: 0, y: 0, w: 100, h: 0 }), [])
})
