import { useMemo } from 'react'
import { useI18n } from '../i18n'
import { useCurrency } from '../lib/currency'
import { pnlTone } from '../lib/format'

interface CalendarProps {
  days: Array<{ date: string; net_pnl_usd: number; trade_count: number }>
  availableMonths: string[]
  selectedMonth: string
  onSelectMonth: (month: string) => void
}

export function MonthlyCalendar({
  days,
  availableMonths,
  selectedMonth,
  onSelectMonth,
}: CalendarProps) {
  const { locale, t } = useI18n()
  const cur = useCurrency()

  const months = useMemo(() => {
    const s = new Set<string>(availableMonths)
    days.forEach(d => {
      if (/^\d{4}-\d{2}/.test(d.date)) s.add(d.date.slice(0, 7))
    })
    const arr = Array.from(s).sort().reverse()
    if (!arr.length) {
      const now = new Date()
      arr.push(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
    }
    return arr
  }, [availableMonths, days])

  const activeMonth = /^\d{4}-\d{2}$/.test(selectedMonth) && months.includes(selectedMonth) ? selectedMonth : months[0]
  const [yStr, mStr] = activeMonth.split('-')
  const year = Number(yStr)
  const mon = Number(mStr)
  const daysInMonth = new Date(year, mon, 0).getDate()
  const firstOffset = (new Date(year, mon - 1, 1).getDay() + 6) % 7

  const map = useMemo(() => {
    const m = new Map<string, { net_pnl_usd: number; trade_count: number }>()
    days.forEach(d => m.set(d.date, d))
    return m
  }, [days])

  let winDays = 0
  let lossDays = 0
  let monthSum = 0
  const cells: Array<{ day: number | null; pnl?: number; tradeCount?: number }> = []
  for (let i = 0; i < firstOffset; i++) cells.push({ day: null })
  for (let d = 1; d <= daysInMonth; d++) {
    const key = `${activeMonth}-${String(d).padStart(2, '0')}`
    const item = map.get(key)
    if (item) {
      monthSum += item.net_pnl_usd
      const isPassiveSubDollar = (item.trade_count ?? 0) === 0 && Math.abs(item.net_pnl_usd) < 0.5
      if (!isPassiveSubDollar) {
        if (item.net_pnl_usd >= 0.005) winDays++
        else if (item.net_pnl_usd <= -0.005) lossDays++
      }
    }
    cells.push({ day: d, pnl: item?.net_pnl_usd, tradeCount: item?.trade_count })
  }

  const weekdays = locale === 'en' ? ['M', 'T', 'W', 'T', 'F', 'S', 'S'] : ['一', '二', '三', '四', '五', '六', '日']

  return (
    <div className="flex flex-col gap-3 h-full">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h2 className="t-h2 whitespace-nowrap">{t('an_cal_title')}</h2>
            <span className="text-[13px] text-[var(--fg-3)] whitespace-nowrap">
              {activeMonth}
            </span>
          </div>
          <div className="text-[13px] text-[var(--fg-3)] whitespace-nowrap mt-0.5">
            {winDays} {t('an_cal_win_days')} · {lossDays} {t('an_cal_loss_days')} · {t('an_cal_month_sum')}{' '}
            <b className={`sensitive ${pnlTone(monthSum)}`}>{cur.signedMoney(monthSum, 2)}</b>
          </div>
        </div>
        <select
          aria-label={t('an_cal_title')}
          value={/^\d{4}-\d{2}$/.test(selectedMonth) ? selectedMonth : months[0]}
          onChange={e => onSelectMonth(e.target.value)}
          className="otl-select h-8 px-2.5 text-[13px] cursor-pointer"
        >
          {months.map(m => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      <div key={activeMonth} className="otl-panel-swap grid grid-cols-7 gap-1.5">
        {weekdays.map((w, i) => (
          <div key={i} className="text-center text-[13px] text-[var(--fg-3)] pb-0.5">
            {w}
          </div>
        ))}
        {cells.map((c, idx) => {
          if (c.day === null) return <div key={`e-${idx}`} className="min-h-[48px]" />
          const has = typeof c.pnl === 'number'
          const rawPnl = c.pnl ?? 0
          const isZeroNoise = has && Math.abs(rawPnl) < 0.005
          const isPassiveDust = has && !isZeroNoise && (c.tradeCount ?? 0) === 0 && Math.abs(rawPnl) < 0.5
          const isSignificant = has && !isZeroNoise && !isPassiveDust
          const isPos = rawPnl > 0
          const cellDecimals = Math.abs(rawPnl * cur.rate) > 0 && Math.abs(rawPnl * cur.rate) < 1 ? 2 : 0
          return (
            <div
              key={c.day}
              className="min-h-[48px] rounded-[5px] border p-1.5 flex flex-col justify-between text-[13px]"
              style={{
                background: isSignificant ? (isPos ? 'var(--pos-soft)' : 'var(--neg-soft)') : 'var(--surface-2)',
                borderColor: isSignificant ? (isPos ? 'var(--pos-bd)' : 'var(--neg-bd)') : 'var(--border)',
              }}
            >
              <span className="text-[var(--fg-3)] leading-none">{c.day}</span>
              {has && !isZeroNoise ? (
                <strong
                  className={`sensitive leading-none whitespace-nowrap ${
                    isSignificant ? pnlTone(rawPnl) : 'text-[var(--fg-3)] font-medium'
                  }`}
                >
                  {cur.signedPlainMoney(rawPnl, cellDecimals)}
                </strong>
              ) : (
                <span className="text-[var(--fg-3)] opacity-40 leading-none">—</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
