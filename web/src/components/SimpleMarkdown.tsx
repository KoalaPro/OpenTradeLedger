import type { ReactNode } from 'react'

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g
  const parts = text.split(pattern)
  return parts.map((part, idx) => {
    const key = `${keyPrefix}-${idx}`
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return (
        <strong key={key} className="font-semibold text-[var(--fg)]">
          {part.slice(2, -2)}
        </strong>
      )
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return (
        <em key={key} className="not-italic font-medium text-[var(--accent)]">
          {part.slice(1, -1)}
        </em>
      )
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return (
        <code
          key={key}
          className="px-1.5 py-0.5 rounded bg-[var(--surface-2)] text-[13px] font-mono text-[var(--fg)]"
        >
          {part.slice(1, -1)}
        </code>
      )
    }
    return <span key={key}>{part}</span>
  })
}

export function SimpleMarkdown({ content }: { content: string }) {
  const lines = (content || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  if (!lines.length) return null

  const blocks: ReactNode[] = []
  let orderedItems: string[] = []
  let bulletItems: string[] = []

  const flushLists = (idx: number) => {
    if (orderedItems.length) {
      blocks.push(
        <ol
          key={`ol-${idx}`}
          className="list-decimal pl-5 space-y-2 text-[14px] leading-relaxed text-[var(--fg-2)]"
        >
          {orderedItems.map((item, i) => (
            <li key={i}>{renderInline(item, `oli-${idx}-${i}`)}</li>
          ))}
        </ol>,
      )
      orderedItems = []
    }
    if (bulletItems.length) {
      blocks.push(
        <ul
          key={`ul-${idx}`}
          className="list-disc pl-5 space-y-1.5 text-[14px] leading-relaxed text-[var(--fg-2)]"
        >
          {bulletItems.map((item, i) => (
            <li key={i}>{renderInline(item, `uli-${idx}-${i}`)}</li>
          ))}
        </ul>,
      )
      bulletItems = []
    }
  }

  lines.forEach((line, idx) => {
    const olMatch = /^\d+\.\s+(.*)$/.exec(line)
    if (olMatch) {
      if (bulletItems.length) flushLists(idx)
      orderedItems.push(olMatch[1])
      return
    }
    const ulMatch = /^[-*•]\s+(.*)$/.exec(line)
    if (ulMatch) {
      if (orderedItems.length) flushLists(idx)
      bulletItems.push(ulMatch[1])
      return
    }
    flushLists(idx)
    blocks.push(
      <p key={`p-${idx}`} className="m-0 text-[14px] leading-relaxed text-[var(--fg-2)]">
        {renderInline(line, `p-${idx}`)}
      </p>,
    )
  })
  flushLists(lines.length)

  return <div className="space-y-2.5">{blocks}</div>
}
