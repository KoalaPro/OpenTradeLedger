import { createContext, useContext, type ReactNode } from 'react'
import type { Locale } from '../lib/types'
import { zhCN, type MessageKey } from './zh-CN'
import { en } from './en'

const dictionaries = {
  'zh-CN': zhCN,
  en,
} as const

interface I18nContextValue {
  locale: Locale
  t: (key: MessageKey) => string
}

const I18nContext = createContext<I18nContextValue>({
  locale: 'zh-CN',
  t: key => zhCN[key],
})

export function I18nProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  const dict = dictionaries[locale] ?? zhCN
  const t = (key: MessageKey) => dict[key] ?? zhCN[key]
  return <I18nContext.Provider value={{ locale, t }}>{children}</I18nContext.Provider>
}

export function useI18n() {
  return useContext(I18nContext)
}
