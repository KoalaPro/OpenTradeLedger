import { createContext, useContext, useMemo, type ReactNode } from 'react'
import type { CurrencyCode, FxRatesPayload } from './types'

export interface CurrencyOption {
  code: CurrencyCode
  symbol: string
  labelZh: string
  labelEn: string
  defaultDecimals: number
}

export const CURRENCY_OPTIONS: CurrencyOption[] = [
  { code: 'USDT', symbol: '', labelZh: 'USDT', labelEn: 'USDT', defaultDecimals: 2 },
  { code: 'USD', symbol: '$', labelZh: '美元 ($)', labelEn: 'USD ($)', defaultDecimals: 2 },
  { code: 'CNY', symbol: '¥', labelZh: '人民币 (¥)', labelEn: 'CNY (¥)', defaultDecimals: 2 },
  { code: 'GBP', symbol: '£', labelZh: '英镑 (£)', labelEn: 'GBP (£)', defaultDecimals: 2 },
  { code: 'EUR', symbol: '€', labelZh: '欧元 (€)', labelEn: 'EUR (€)', defaultDecimals: 2 },
  { code: 'HKD', symbol: 'HK$', labelZh: '港币 (HK$)', labelEn: 'HKD (HK$)', defaultDecimals: 2 },
  { code: 'JPY', symbol: 'JP¥', labelZh: '日元 (JP¥)', labelEn: 'JPY (JP¥)', defaultDecimals: 0 },
]

export const DEFAULT_FX: FxRatesPayload = {
  base: 'USD',
  source: 'default',
  updated_at: '',
  rates: {
    USDT: 1,
    USD: 1,
    CNY: 7.2,
    GBP: 0.78,
    EUR: 0.92,
    HKD: 7.8,
    JPY: 155,
  },
}

interface CurrencyContextValue {
  code: CurrencyCode
  symbol: string
  rate: number
  fx: FxRatesPayload
  /** Formats converted amount with currency sign/unit (e.g. "24,680.50 U" or "¥164,898.94") */
  money: (usd: number, decimals?: number) => string
  /** Formats signed converted amount with currency sign/unit (e.g. "+2,680.50 U" or "+¥17,909.34") */
  signedMoney: (usd: number, decimals?: number) => string
  /** Formats converted amount without trailing " U" for hero KPIs / tables where unit is in badge/header */
  plainMoney: (usd: number, decimals?: number) => string
  /** Formats signed converted amount without trailing " U" */
  signedPlainMoney: (usd: number, decimals?: number) => string
  /** Converts embedded "+23.39 U" / "-358.83 USDT" tokens inside descriptive strings to active display currency */
  convertText: (text: string) => string
}

function buildHelpers(code: CurrencyCode, fx: FxRatesPayload): CurrencyContextValue {
  const opt = CURRENCY_OPTIONS.find(o => o.code === code) ?? CURRENCY_OPTIONS[0]
  const rate = Number(fx.rates?.[code] ?? 1) || 1
  const isUsdt = code === 'USDT'

  const resolveDecimals = (decimals?: number) => {
    if (code === 'JPY') {
      return typeof decimals === 'number' && decimals > 2 ? 1 : 0
    }
    if (typeof decimals === 'number') {
      return decimals
    }
    return opt.defaultDecimals
  }

  const formatNum = (val: number, d: number) =>
    val.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

  const plainMoney = (usd: number, decimals?: number) => {
    const safe = Number.isFinite(usd) ? usd * rate : 0
    const d = resolveDecimals(decimals)
    const sign = safe < 0 ? '-' : ''
    return `${sign}${opt.symbol}${formatNum(Math.abs(safe), d)}`
  }

  const signedPlainMoney = (usd: number, decimals?: number) => {
    const safe = Number.isFinite(usd) ? usd * rate : 0
    const d = resolveDecimals(decimals)
    const sign = safe > 0 ? '+' : safe < 0 ? '-' : ''
    return `${sign}${opt.symbol}${formatNum(Math.abs(safe), d)}`
  }

  const money = (usd: number, decimals?: number) => {
    const base = plainMoney(usd, decimals)
    return isUsdt ? `${base} U` : base
  }

  const signedMoney = (usd: number, decimals?: number) => {
    const base = signedPlainMoney(usd, decimals)
    return isUsdt ? `${base} U` : base
  }

  const convertText = (text: string) => {
    if (!text || isUsdt) return text
    // 1. Convert explicit U / USDT amounts (e.g. "+23.39 USDT", "-49.57 U", "53.32 U")
    let out = text.replace(/([+-]?\d[\d,]*(?:\.\d+)?)\s*U(?:SDT)?\b/g, (_m, numStr: string) => {
      const parsed = Number(numStr.replace(/,/g, ''))
      if (!Number.isFinite(parsed)) return _m
      return numStr.startsWith('+') || numStr.startsWith('-') ? signedMoney(parsed) : money(parsed)
    })
    // 2. Convert standalone signed 2-decimal ledger components (e.g. "账面 -358.83 + 手操返佣 +157.86")
    out = out.replace(/(^|[\s(])([+-]\d+(?:\.\d{2}))(?![\d.%A-Za-z])/g, (full, pre: string, numStr: string) => {
      const parsed = Number(numStr)
      if (!Number.isFinite(parsed)) return full
      return `${pre}${signedMoney(parsed)}`
    })
    return out
  }

  return {
    code,
    symbol: opt.symbol,
    rate,
    fx,
    money,
    signedMoney,
    plainMoney,
    signedPlainMoney,
    convertText,
  }
}

const CurrencyContext = createContext<CurrencyContextValue>(buildHelpers('USDT', DEFAULT_FX))

export function CurrencyProvider({
  currency,
  fx,
  children,
}: {
  currency: CurrencyCode
  fx: FxRatesPayload
  children: ReactNode
}) {
  const value = useMemo(() => buildHelpers(currency, fx), [currency, fx])
  return <CurrencyContext.Provider value={value}>{children}</CurrencyContext.Provider>
}

export function useCurrency() {
  return useContext(CurrencyContext)
}
