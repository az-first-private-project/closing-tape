/**
 * 轻量 Markdown 渲染（安全，不使用 dangerouslySetInnerHTML）
 * ===========================================================
 * 把后端 AI 日报的 Markdown 文本按行解析成 React 元素，支持：
 *   - 标题 # / ## / ### / ####
 *   - 无序列表 - / *，有序列表 1.
 *   - 引用 >、分割线 ---
 *   - 围栏代码块 ```lang
 *   - GFM 表格（含 :-- / :-: / --: 对齐）
 *   - 行内 **加粗**、`代码`、*斜体*、[链接](url)、![图片](url)
 * 所有文本都作为 React children 渲染，天然转义 HTML；链接/图片只放行
 * http(s)、mailto、锚点与相对路径，阻断 javascript:/data: 等危险协议。
 */
import { Fragment, type ReactNode } from 'react'

/** 链接与图片只允许这些协议；无协议视为相对路径 */
function isSafeUrl(raw: string): boolean {
  const url = raw.trim()
  if (!url) return false
  if (/^\s*(javascript|data|vbscript|file):/i.test(url)) return false
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) {
    return /^https?:/i.test(url) || /^mailto:/i.test(url)
  }
  return true
}

const INLINE_PATTERN =
  /(!\[[^\]]*\]\([^)\s]+\)|\[[^\]]+\]\([^)\s]+\)|\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g

/** 行内格式解析：图片 / 链接 / **加粗** / `代码` / *斜体* */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(INLINE_PATTERN).filter((part) => part !== '')
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`

    const image = /^!\[([^\]]*)\]\(([^)\s]+)\)$/.exec(part)
    if (image) {
      const [, alt, src] = image
      if (!isSafeUrl(src)) {
        return (
          <span key={key} className="text-muted-foreground">
            {alt || '图片'}
          </span>
        )
      }
      return (
        <img
          key={key}
          src={src}
          alt={alt}
          loading="lazy"
          referrerPolicy="no-referrer"
          className="my-3 max-h-[420px] max-w-full rounded-lg border border-border/60"
        />
      )
    }

    const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part)
    if (link) {
      const [, label, href] = link
      if (!isSafeUrl(href)) {
        return <Fragment key={key}>{label}</Fragment>
      }
      return (
        <a
          key={key}
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="text-brass-strong underline decoration-brass/40 underline-offset-2 transition-colors hover:text-brass dark:text-brass"
        >
          {renderInline(label, `${key}-label`)}
        </a>
      )
    }

    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={key} className="font-semibold text-foreground">
          {part.slice(2, -2)}
        </strong>
      )
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code key={key} className="rounded border border-border/60 bg-muted/60 px-1 py-0.5 font-mono text-[0.82em] text-foreground">
          {part.slice(1, -1)}
        </code>
      )
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return (
        <em key={key} className="italic">
          {part.slice(1, -1)}
        </em>
      )
    }
    return <Fragment key={key}>{part}</Fragment>
  })
}

/** 表格单元格拆分：支持 \| 转义，忽略首尾竖线 */
function splitTableRow(row: string): string[] {
  let trimmed = row.trim()
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1)
  if (trimmed.endsWith('|') && !trimmed.endsWith('\\|')) trimmed = trimmed.slice(0, -1)
  const cells: string[] = []
  let current = ''
  for (let index = 0; index < trimmed.length; index += 1) {
    const char = trimmed[index]
    if (char === '\\' && trimmed[index + 1] === '|') {
      current += '|'
      index += 1
      continue
    }
    if (char === '|') {
      cells.push(current.trim())
      current = ''
      continue
    }
    current += char
  }
  cells.push(current.trim())
  return cells
}

function isTableSeparator(line: string): boolean {
  if (!line.includes('-')) return false
  const cells = splitTableRow(line)
  return cells.length > 0 && cells.every((cell) => /^:?-{2,}:?$/.test(cell))
}

function cellAlignment(cell: string): 'text-left' | 'text-center' | 'text-right' {
  const left = cell.startsWith(':')
  const right = cell.endsWith(':')
  if (left && right) return 'text-center'
  if (right) return 'text-right'
  return 'text-left'
}

interface MarkdownProps {
  text: string
  className?: string
}

export default function Markdown({ text, className = '' }: MarkdownProps) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let listItems: string[] = []
  let listOrdered = false
  let paragraphLines: string[] = []
  let blockIndex = 0

  const flushList = () => {
    if (listItems.length === 0) return
    const Tag = listOrdered ? 'ol' : 'ul'
    const items = listItems
    listItems = []
    blocks.push(
      <Tag
        key={`list-${blockIndex++}`}
        className={`${listOrdered ? 'list-decimal' : 'list-disc'} my-2.5 space-y-1.5 pl-4 text-[13px] leading-relaxed text-muted-foreground marker:text-brass/60`}
      >
        {items.map((item, index) => (
          <li key={index}>{renderInline(item, `li-${blockIndex}-${index}`)}</li>
        ))}
      </Tag>,
    )
  }

  const flushParagraph = () => {
    if (paragraphLines.length === 0) return
    const content = paragraphLines.join(' ')
    paragraphLines = []
    blocks.push(
      <p key={`p-${blockIndex++}`} className="my-2 text-[13px] leading-[1.85] text-muted-foreground">
        {renderInline(content, `p-${blockIndex}`)}
      </p>,
    )
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trimEnd()
    const trimmed = line.trim()

    if (trimmed === '') {
      flushParagraph()
      flushList()
      continue
    }

    // 围栏代码块 ```lang ... ```
    if (trimmed.startsWith('```')) {
      flushParagraph()
      flushList()
      const language = trimmed.slice(3).trim().replace(/[^\w#+.-]/g, '').slice(0, 24)
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index])
        index += 1
      }
      const key = `code-${blockIndex++}`
      blocks.push(
        <div key={key} className="my-3 overflow-hidden rounded-lg border border-border/60 bg-muted/40">
          {language && (
            <div className="border-b border-border/60 px-3 py-1.5 font-mono text-[12px] tracking-[0.06em] text-muted-foreground">
              {language}
            </div>
          )}
          <pre className="overflow-x-auto p-3 text-[12px] leading-relaxed">
            <code className="font-mono text-foreground/90">{codeLines.join('\n')}</code>
          </pre>
        </div>,
      )
      continue
    }

    // GFM 表格：表头行 + 分隔行
    if (
      trimmed.startsWith('|') &&
      index + 1 < lines.length &&
      isTableSeparator(lines[index + 1].trim())
    ) {
      flushParagraph()
      flushList()
      const headers = splitTableRow(trimmed)
      const aligns = splitTableRow(lines[index + 1].trim()).map(cellAlignment)
      const rows: string[][] = []
      index += 2
      while (index < lines.length) {
        const rowLine = lines[index].trim()
        if (!rowLine.startsWith('|')) break
        if (isTableSeparator(rowLine)) {
          index += 1
          continue
        }
        rows.push(splitTableRow(rowLine))
        index += 1
      }
      // while 结束时 index 指向首个非表格行，外层循环会再 ++，因此回退一步
      index -= 1
      const key = `table-${blockIndex++}`
      blocks.push(
        <div key={key} className="my-3 max-w-full overflow-x-auto rounded-lg border border-border/60">
          <table className="w-full min-w-[420px] border-collapse text-[12.5px] leading-relaxed">
            <thead className="bg-muted/50">
              <tr>
                {headers.map((header, cellIndex) => (
                  <th
                    key={cellIndex}
                    scope="col"
                    className={`border-b border-border/60 px-3 py-2 font-semibold text-foreground ${aligns[cellIndex] ?? 'text-left'}`}
                  >
                    {renderInline(header, `${key}-h-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-b border-border/40 last:border-0">
                  {headers.map((_, cellIndex) => (
                    <td
                      key={cellIndex}
                      className={`px-3 py-2 align-top text-muted-foreground ${aligns[cellIndex] ?? 'text-left'}`}
                    >
                      {renderInline(row[cellIndex] ?? '', `${key}-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    // 标题
    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      const content = heading[2]
      const className =
        level === 1
          ? 'mt-6 mb-2 text-[16px] font-semibold tracking-[-0.015em] text-foreground'
          : level === 2
            ? 'mt-5 mb-1.5 text-[13px] font-semibold text-foreground'
            : 'mt-4 mb-1 text-[12px] font-semibold tracking-[0.04em] text-muted-foreground'
      const key = `h-${blockIndex++}`
      if (level === 1) blocks.push(<h2 key={key} className={className}>{renderInline(content, key)}</h2>)
      else if (level === 2) blocks.push(<h3 key={key} className={className}>{renderInline(content, key)}</h3>)
      else if (level === 3) blocks.push(<h4 key={key} className={className}>{renderInline(content, key)}</h4>)
      else blocks.push(<h5 key={key} className={className}>{renderInline(content, key)}</h5>)
      continue
    }

    // 分割线
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      flushParagraph()
      flushList()
      blocks.push(<hr key={`hr-${blockIndex++}`} className="my-5 border-border/60" />)
      continue
    }

    // 引用
    const quote = /^>\s?(.*)$/.exec(trimmed)
    if (quote) {
      flushParagraph()
      flushList()
      blocks.push(
        <blockquote
          key={`quote-${blockIndex++}`}
          className="my-3 border-l-2 border-brass/60 pl-3.5 text-[13px] italic leading-relaxed text-muted-foreground"
        >
          {renderInline(quote[1], `quote-${blockIndex}`)}
        </blockquote>,
      )
      continue
    }

    // 无序列表
    const bullet = /^[-*]\s+(.+)$/.exec(trimmed)
    if (bullet) {
      flushParagraph()
      if (listItems.length > 0 && listOrdered) flushList()
      listOrdered = false
      listItems.push(bullet[1])
      continue
    }

    // 有序列表
    const ordered = /^\d+[.)]\s+(.+)$/.exec(trimmed)
    if (ordered) {
      flushParagraph()
      if (listItems.length > 0 && !listOrdered) flushList()
      listOrdered = true
      listItems.push(ordered[1])
      continue
    }

    // 普通段落行
    flushList()
    paragraphLines.push(trimmed)
  }

  flushParagraph()
  flushList()

  return <div className={`space-y-1 ${className}`}>{blocks}</div>
}
