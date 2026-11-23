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
}

function buildHelpers(code: CurrencyCode, fx: FxRatesPayload): CurrencyContextValue {
  const opt = CURRENCY_OPTIONS.find(o => o.code === code) ?? CURRENCY_OPTIONS[0]
  const rate = Number(fx.rates?.[code] ?? 1) || 1
  const isUsdt = code === 'USDT'

  const resolveDecimals = (decimals?: number) => {
    if (typeof decimals === 'number') {
      return code === 'JPY' && decimals === 2 ? 0 : decimals
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

  return {
    code,
    symbol: opt.symbol,
    rate,
    fx,
    money,
    signedMoney,
    plainMoney,
    signedPlainMoney,
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
