import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { CurrencyProvider, DEFAULT_FX, useCurrency } from '../lib/currency'
import { TradeDrilldownDrawer } from '../components/TradeDrilldownDrawer'
import { FloatingModal } from '../components/FloatingModal'
import { PublicCopyWatcherPanel } from '../components/PublicCopyWatcherPanel'
import { api } from '../lib/api'
import type { CopyWatcherStatusPayload, CopyWatcherTargetItem } from '../lib/types'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('only converts USD source amounts in a mixed-unit review', () => {
  function Review() {
    return <p>{useCurrency().convertText('+104.00 USDT / -$3 USD / 2 U / +1.50 bps / +0.75 R / +2.00% / +3.40 / 0.12 BTC / 3 ETH')}</p>
  }
  render(<CurrencyProvider currency="CNY" fx={DEFAULT_FX}><Review /></CurrencyProvider>)
  expect(screen.getByRole('paragraph').textContent).toBe('+¥748.80 / -¥21.60 / ¥14.40 / +1.50 bps / +0.75 R / +2.00% / +3.40 / 0.12 BTC / 3 ETH')
})

it('distinguishes an unavailable symbol history from an empty history, and retries in place', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response('unavailable', { status: 503 }))
    .mockResolvedValueOnce(Response.json({ open_positions: [], trades: [] }))
  render(<TradeDrilldownDrawer open venueId="okx" symbol="ETH_USDT" rangeKey="all" onClose={() => {}} onOpenInTrades={() => {}} />)
  const retry = await screen.findByRole('button', { name: '重试' })
  expect(screen.queryByText(/暂无平仓记录/)).toBeNull()
  fireEvent.click(retry)
  expect(await screen.findByText(/暂无平仓记录/)).toBeTruthy()
  expect(screen.getByRole('dialog').textContent).toContain('ETH_USDT')
})

it('keeps keyboard navigation inside the asset dialog and restores its opener', () => {
  function Overview() {
    const [open, setOpen] = useState(false)
    return <><button onClick={() => setOpen(true)}>净资产</button><button>背后动作</button>
      <FloatingModal open={open} title="资产构成" onClose={() => setOpen(false)}><button>钱包</button></FloatingModal></>
  }
  render(<Overview />)
  const opener = screen.getByText('净资产')
  opener.focus()
  fireEvent.click(opener)
  const dialog = screen.getByRole('dialog', { name: '资产构成' })
  const last = screen.getByText('钱包')
  expect(dialog.contains(document.activeElement)).toBe(true)
  last.focus()
  fireEvent.keyDown(last, { key: 'Tab' })
  expect(document.activeElement).toBe(dialog.querySelector('button'))
  fireEvent.keyDown(document.activeElement!, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(last)
  fireEvent.keyDown(last, { key: 'Escape' })
  expect(document.activeElement).toBe(opener)
})

function target(id: string, enabled = true): CopyWatcherTargetItem {
  return { portfolio_id: id, venue: 'binance', nickname: id, status: 'ACTIVE', portfolio_type: 'PUBLIC',
    enabled, slot_alert: false, trade_alert: false, current_copy_count: 0, max_copy_count: 10,
    available_slots: 10, has_open_slot: true, min_copy_usd: 10, profit_sharing_rate: '10',
    lead_url: '', app_deeplink: '', last_alert_ms: 0, inferred_positions: [], recent_orders: [] }
}

it('keeps both watchers paused when the first target responds after the second', async () => {
  const initial: CopyWatcherStatusPayload = {
    demo_mode: false, watcher_running: true, interval_sec: 10, trade_poll_sec: 30, cooldown_sec: 180,
    updated_at_ms: 1, open_slot_count: 2, channels: { bark: true }, configured_targets: ['A', 'B'],
    items: [target('A'), target('B')],
  }
  vi.spyOn(api, 'getCopyWatcher').mockResolvedValue(initial)
  let finishFirst!: (value: Awaited<ReturnType<typeof api.patchCopyWatcherTarget>>) => void
  vi.spyOn(api, 'patchCopyWatcherTarget')
    .mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
    .mockResolvedValueOnce({ ...initial, ok: true, items: [target('A', false), target('B', false)] })
  render(<PublicCopyWatcherPanel />)
  const a = await screen.findByTestId('toggle-enabled-A')
  const b = screen.getByTestId('toggle-enabled-B')
  fireEvent.click(a)
  await act(async () => { fireEvent.click(b) })
  expect(b.getAttribute('aria-checked')).toBe('false')
  await act(async () => { finishFirst({ ...initial, ok: true, items: [target('A', false), target('B')] }) })
  expect(a.getAttribute('aria-checked')).toBe('false')
  expect(b.getAttribute('aria-checked')).toBe('false')
})
