import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { MonthlyCalendar } from '../components/MonthlyCalendar'

it('switches between the actual daily contract contributions and keeps portfolio totals distinct', () => {
  const position = { trade_id: 'p', venue_id: 'okx', symbol: 'ETH_USDT', side: 'LONG', exit_count: 1 }
  const days = [
    { date: '2026-11-16', net_pnl_usd: -0.2, trade_count: 0, trade_activity: [{ ...position, amount_usd: -0.2, exit_count: 0 }] },
    { date: '2026-11-17', net_pnl_usd: 15.1, trade_count: 1, trade_activity: [{ ...position, amount_usd: 9.9 }] },
    { date: '2026-11-25', net_pnl_usd: 29.9, trade_count: 1, trade_activity: [{ ...position, amount_usd: 29.9 }] },
  ]
  render(<MonthlyCalendar days={days} availableMonths={['2026-11']} selectedMonth="2026-11" onSelectMonth={() => {}} />)
  fireEvent.click(screen.getByRole('button', {name: /^17 /}))
  const activity = () => within(screen.getByRole('list', {name: '当日合约贡献'}))
  expect(activity().getByText('ETH_USDT')).toBeTruthy()
  expect(activity().getByText(/\+9\.90/)).toBeTruthy()
  expect(screen.getByText(/\+15\.10/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', {name: /^25 /}))
  expect(activity().getByText(/\+29\.90/)).toBeTruthy()
  expect(activity().queryByText(/\+9\.90/)).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: /^16 /}))
  expect(activity().getByText(/-0\.20/)).toBeTruthy()
  expect(activity().queryByText(/平仓/)).toBeNull()
})
