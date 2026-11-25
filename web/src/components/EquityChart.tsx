import { useEffect, useRef } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useCurrency } from '../lib/currency'

export interface CurvePoint {
  seq: number
  date: string
  label: string
  delta_usd: number
  cum_net_usd: number
  cum_book_usd: number
}

export function EquityChart({ points, height = 220 }: { points: CurvePoint[]; height?: number }) {
  const cur = useCurrency()
  const prevSigRef = useRef<string>('')

  const curveSig = points.length
    ? `${points.length}:${points[0]?.date ?? ''}:${points[points.length - 1]?.date ?? ''}`
    : 'empty'

  const reducedMotion =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const shouldAnimate = !reducedMotion && curveSig !== prevSigRef.current

  useEffect(() => {
    prevSigRef.current = curveSig
  }, [curveSig])

  if (!points.length) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-[var(--border)] bg-[var(--surface-2)] text-[13px] text-[var(--fg-3)]"
        style={{ height }}
      >
        —
      </div>
    )
  }

  const lastNet = points[points.length - 1]?.cum_net_usd ?? 0
  const isPositive = lastNet >= 0
  const strokeColor = isPositive ? 'var(--pos)' : 'var(--neg)'
  const gradId = isPositive ? 'otlAreaPos' : 'otlAreaNeg'

  return (
    <div style={{ width: '100%', height }} className="sensitive">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor={strokeColor}
                stopOpacity={isPositive ? 0.22 : 0.02}
              />
              <stop
                offset="100%"
                stopColor={strokeColor}
                stopOpacity={isPositive ? 0.01 : 0.18}
              />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: 'var(--fg-3)', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            minTickGap={24}
          />
          <YAxis
            tick={{ fill: 'var(--fg-3)', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={68}
            tickFormatter={v => cur.signedPlainMoney(Number(v), 0)}
          />
          <ReferenceLine y={0} stroke="var(--border-strong)" strokeDasharray="3 3" />
          <Tooltip
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--border-strong)',
              borderRadius: 8,
              fontSize: 13,
              color: 'var(--fg)',
            }}
            formatter={(val: number | string) => {
              const masked =
                typeof document !== 'undefined' && document.documentElement.dataset.privacy === 'on'
              return [masked ? '****' : cur.signedMoney(Number(val), 2), cur.code]
            }}
            labelFormatter={(label, payload) => payload?.[0]?.payload?.date || String(label)}
          />
          <Area
            type="monotone"
            dataKey="cum_net_usd"
            baseValue={0}
            stroke={strokeColor}
            strokeWidth={1.9}
            fill={`url(#${gradId})`}
            isAnimationActive={shouldAnimate}
            animationDuration={520}
            animationEasing="ease-out"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
