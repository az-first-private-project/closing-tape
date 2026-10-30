/**
 * Squarified treemap 布局（V6 T4，手写，不依赖 d3/echarts）
 * ==========================================================
 * 输入一组 id + 权重，输出填满矩形盒子的方块。算法按 Bruls et al. 的
 * squarified 思路：每次把当前行摊到短边上，选让最差长宽比最小的组合，
 * 让方块尽量接近正方形，便于点击与阅读。
 *
 * 约定：
 *  - value 必须是有限正数；0/负值/NaN 直接过滤（调用方不需要先清洗）；
 *  - 输出顺序与权重降序一致；
 *  - 面积严格按 value 比例分配（浮点误差除外），所有方块不重叠。
 */

export interface TreeInput {
  id: string
  value: number
}

export interface TreeBox {
  x: number
  y: number
  w: number
  h: number
}

export interface TreeRect extends TreeBox {
  id: string
}

interface Node {
  id: string
  area: number
}

/** 行的最差长宽比；side 为当前盒子短边 */
function worstRatio(row: Node[], side: number): number {
  if (!row.length || side <= 0) return Infinity
  let sum = 0
  let max = 0
  let min = Infinity
  for (const node of row) {
    sum += node.area
    if (node.area > max) max = node.area
    if (node.area < min) min = node.area
  }
  if (sum <= 0 || min <= 0) return Infinity
  const side2 = side * side
  const sum2 = sum * sum
  return Math.max((side2 * max) / sum2, sum2 / (side2 * min))
}

/**
 * 把 items 铺进 box。
 * 返回的矩形覆盖 box（面积守恒），互不重叠。
 */
export function squarify(items: TreeInput[], box: TreeBox): TreeRect[] {
  const bw = box.w
  const bh = box.h
  if (!(bw > 0) || !(bh > 0)) return []

  const valid = items.filter(
    (item) => item && Number.isFinite(item.value) && item.value > 0,
  )
  if (!valid.length) return []

  const total = valid.reduce((sum, item) => sum + item.value, 0)
  if (!(total > 0) || !Number.isFinite(total)) return []

  const scale = (bw * bh) / total
  const nodes: Node[] = valid
    .map((item) => ({ id: item.id, area: item.value * scale }))
    .sort((left, right) => right.area - left.area)

  const rects: TreeRect[] = []
  let x = box.x
  let y = box.y
  let w = bw
  let h = bh

  const flush = (row: Node[]) => {
    if (!row.length) return
    const area = row.reduce((sum, node) => sum + node.area, 0)
    if (w >= h) {
      // 竖条：宽度 = 行面积 / 高度，沿 y 堆叠
      const stripWidth = h > 0 ? area / h : 0
      let cursor = y
      for (const node of row) {
        const rectHeight = stripWidth > 0 ? node.area / stripWidth : 0
        rects.push({ id: node.id, x, y: cursor, w: stripWidth, h: rectHeight })
        cursor += rectHeight
      }
      x += stripWidth
      w -= stripWidth
    } else {
      // 横条：高度 = 行面积 / 宽度，沿 x 堆叠
      const stripHeight = w > 0 ? area / w : 0
      let cursor = x
      for (const node of row) {
        const rectWidth = stripHeight > 0 ? node.area / stripHeight : 0
        rects.push({ id: node.id, x: cursor, y, w: rectWidth, h: stripHeight })
        cursor += rectWidth
      }
      y += stripHeight
      h -= stripHeight
    }
  }

  let row: Node[] = []
  for (const node of nodes) {
    if (!row.length) {
      row = [node]
      continue
    }
    const side = Math.min(w, h)
    if (worstRatio([...row, node], side) <= worstRatio(row, side)) {
      row.push(node)
    } else {
      flush(row)
      row = [node]
    }
  }
  flush(row)

  return rects
}
