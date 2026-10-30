/**
 * CSV 导出工具（纯前端表格导出用；服务端导出见 api/client.ts 的 downloadCsv）
 */

/** 单元格转 CSV 文本：处理引号、逗号、换行 */
function escapeCell(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}

/** 二维数组 → CSV 字符串（带 BOM，Excel 打开中文不乱码） */
export function toCsv(headers: string[], rows: string[][]): string {
  const lines = [headers.map(escapeCell).join(','), ...rows.map((row) => row.map(escapeCell).join(','))]
  return `\uFEFF${lines.join('\r\n')}`
}

/** 触发浏览器下载文本文件 */
export function downloadText(text: string, filename: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
