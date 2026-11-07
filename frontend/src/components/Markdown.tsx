/**
 * 轻量 Markdown 渲染（安全，不使用 dangerouslySetInnerHTML）
 * ===========================================================
 * 把后端 AI 日报的 Markdown 文本按行解析成 React 元素，支持：
 *   - 标题 # / ## / ###
 *   - 无序列表 - / *，有序列表 1.
 *   - 引用 >、分割线 ---
 *   - 行内 **加粗**、`代码`、*斜体*
 * 所有文本都作为 React children 渲染，天然转义 HTML，杜绝 XSS。
 */
import { Fragment, type ReactNode } from 'react'

/** 行内格式解析：**加粗** / `代码` / *斜体* */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g
  const parts = text.split(pattern).filter((part) => part !== '')
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`
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

  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    const trimmed = line.trim()

    if (trimmed === '') {
      flushParagraph()
      flushList()
      continue
    }

    // 标题
    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed)
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
            : 'mt-4 mb-1 text-[11px] font-semibold tracking-[0.04em] text-muted-foreground'
      const key = `h-${blockIndex++}`
      if (level === 1) blocks.push(<h2 key={key} className={className}>{renderInline(content, key)}</h2>)
      else if (level === 2) blocks.push(<h3 key={key} className={className}>{renderInline(content, key)}</h3>)
      else blocks.push(<h4 key={key} className={className}>{renderInline(content, key)}</h4>)
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
