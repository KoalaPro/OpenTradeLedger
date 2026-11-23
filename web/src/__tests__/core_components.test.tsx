import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nProvider } from '../i18n'
import { Sidebar } from '../components/Sidebar'
import { SimpleMarkdown } from '../components/SimpleMarkdown'
import { fmtSignedUsd, fmtUsd } from '../lib/format'

describe('OpenTradeLedger Core Components', () => {
  it('formats numbers consistently with thousand separators', () => {
    expect(fmtUsd(2700.2)).toBe('2,700.20')
    expect(fmtSignedUsd(-2700.2)).toBe('-2,700.20')
    expect(fmtSignedUsd(185.96)).toBe('+185.96')
  })

  it('renders SimpleMarkdown safely without raw asterisks or script execution', () => {
    const md = '1. **Reduce taker drag**: Use *0-bps maker* venues.\n2. Keep `<script>alert(1)</script>` escaped.'
    const { container } = render(<SimpleMarkdown content={md} />)
    expect(container.querySelector('ol')).toBeTruthy()
    expect(container.querySelector('strong')?.textContent).toBe('Reduce taker drag')
    expect(container.querySelector('em')?.textContent).toBe('0-bps maker')
    expect(container.textContent).not.toContain('*0-bps maker*')
    expect(container.querySelector('script')).toBeNull()
  })

  it('renders clean single-line Sidebar navigation without count sublabels or hardcoded venue counts', () => {
    render(
      <I18nProvider locale="zh-CN">
        <Sidebar
          page="overview"
          onNavigate={vi.fn()}
          showExtensions={true}
          lastSyncMs={0}
          manualSyncing={false}
          onSync={vi.fn()}
        />
      </I18nProvider>,
    )
    expect(screen.getByTestId('brand-title').textContent).toBe('交易账本')
    const sidebar = screen.getByTestId('sidebar')
    expect(sidebar.textContent).not.toContain('5所')
    expect(sidebar.textContent).not.toContain('3所')
    expect(screen.getByTestId('nav-trades').textContent).toBe('合约交易')
    expect(screen.getByTestId('nav-extensions').textContent).toBe('扩展账本')
  })
})
