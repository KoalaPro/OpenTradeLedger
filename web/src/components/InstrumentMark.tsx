import { useState } from 'react'
import ake from '../assets/instruments/AKE.svg'
import anthropic from '../assets/instruments/ANTHROPIC.svg'
import b from '../assets/instruments/B.svg'
import bank from '../assets/instruments/BANK.svg'
import beat from '../assets/instruments/BEAT.svg'
import btc from '../assets/instruments/BTC.svg'
import crude from '../assets/instruments/CRUDE.svg'
import dell from '../assets/instruments/DELL.svg'
import dexe from '../assets/instruments/DEXE.svg'
import dot from '../assets/instruments/DOT.svg'
import elsa from '../assets/instruments/ELSA.svg'
import esports from '../assets/instruments/ESPORTS.svg'
import eth from '../assets/instruments/ETH.svg'
import evaa from '../assets/instruments/EVAA.svg'
import fil from '../assets/instruments/FIL.svg'
import giggle from '../assets/instruments/GIGGLE.svg'
import gold from '../assets/instruments/GOLD.svg'
import irys from '../assets/instruments/IRYS.svg'
import kaia from '../assets/instruments/KAIA.svg'
import myx from '../assets/instruments/MYX.svg'
import qnt from '../assets/instruments/QNT.svg'
import silver from '../assets/instruments/SILVER.svg'
import skhynix from '../assets/instruments/SKHYNIX.svg'
import sndk from '../assets/instruments/SNDK.svg'
import spcx from '../assets/instruments/SPCX.svg'
import trump from '../assets/instruments/TRUMP.svg'
import usdt from '../assets/instruments/USDT.svg'
import vanry from '../assets/instruments/VANRY.png'
import wld from '../assets/instruments/WLD.svg'
import zec from '../assets/instruments/ZEC.svg'

function svgUri(svg: string): string {
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

const BUILTIN_SVGS = {
  SOL: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#131822"/><defs><linearGradient id="s" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#14F195"/><stop offset="100%" stop-color="#9945FF"/></linearGradient></defs><path fill="url(#s)" d="M9.5 20.6l2.1-2.2h10.9l-2.1 2.2H9.5zm0-7l2.1-2.2h10.9l-2.1 2.2H9.5zm13 3.5l-2.1-2.2H9.5l2.1 2.2h10.9z"/></svg>',
  ),
  DOGE: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#C2A633"/><path fill="#fff" d="M11 9.5h5.4c3.9 0 6.4 2.5 6.4 6.5s-2.5 6.5-6.4 6.5H11V9.5zm3 2.5v3h3.2v1.8H14V20h2.3c2.2 0 3.5-1.5 3.5-4s-1.3-4-3.5-4H14zm-4.5 3h3v1.8h-3V15z"/></svg>',
  ),
  BNB: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#F0B90B"/><path fill="#fff" d="M16 7.8l4.7 4.7-1.8 1.8-2.9-2.9-2.9 2.9-1.8-1.8L16 7.8zm-6.4 6.4l1.8 1.8-1.8 1.8-1.8-1.8 1.8-1.8zm12.8 0l1.8 1.8-1.8 1.8-1.8-1.8 1.8-1.8zM16 14.2l1.8 1.8-1.8 1.8-1.8-1.8 1.8-1.8zm-2.9 3.5l2.9 2.9 2.9-2.9 1.8 1.8L16 24.2l-4.7-4.7 1.8-1.8z"/></svg>',
  ),
  GT: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#2354E6"/><path fill="#fff" d="M16 8a8 8 0 1 0 8 8h-4a4 4 0 1 1-4-4V8z"/><rect x="16" y="12" width="4" height="4" fill="#17E6A1"/></svg>',
  ),
  USDC: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#2775CA"/><path fill="#fff" d="M17.2 14.8c-2-.5-2.5-.9-2.5-1.7 0-.8.7-1.3 1.9-1.3 1.1 0 1.8.4 2.1 1.2h2.2c-.3-1.7-1.4-2.8-3.2-3.1V8h-2.1v1.9c-1.9.3-3.2 1.5-3.2 3.2 0 2 1.4 2.8 3.8 3.4 1.9.5 2.4 1 2.4 1.8 0 .9-.8 1.5-2.1 1.5-1.5 0-2.2-.6-2.4-1.5h-2.2c.3 1.8 1.5 3 3.5 3.3V24h2.1v-1.9c2-.3 3.4-1.5 3.4-3.4 0-2-1.2-3.2-3.7-3.9z"/></svg>',
  ),
  XRP: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#1E2329"/><path fill="#fff" d="M21.8 9h2.5l-5.5 5.4a3.9 3.9 0 0 1-5.6 0L7.7 9h2.5l4.2 4.1a2.2 2.2 0 0 0 3.2 0L21.8 9zM10.2 23H7.7l5.5-5.4a3.9 3.9 0 0 1 5.6 0L24.3 23h-2.5l-4.2-4.1a2.2 2.2 0 0 0-3.2 0L10.2 23z"/></svg>',
  ),
  NVDA: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#76B900"/><path fill="#fff" d="M9.5 16.2c1.8-2.9 4.7-4.5 8-4.5 2.7 0 4.8 1.2 6.2 2.9-1.5 1.8-3.7 3.1-6.2 3.1-1.9 0-3.5-.7-4.6-1.8 1.1-.9 2.6-1.4 4.3-1.4 1.3 0 2.5.4 3.4 1.1l1.4-1.5c-1.3-1-2.9-1.6-4.8-1.6-3.1 0-5.7 1.5-7.7 3.7zm8 5.3c-3.3 0-6.1-1.6-7.9-4.2l-1.3 1.4c2.2 2.9 5.5 4.8 9.2 4.8 3.2 0 6.1-1.4 8.2-3.7l-1.4-1.4c-1.7 1.9-4.1 3.1-6.8 3.1z"/></svg>',
  ),
  AAPL: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#1D222B"/><path fill="#F4F5F8" d="M19.8 16.6c0-2 1.6-3 1.7-3.1-.9-1.4-2.4-1.6-2.9-1.6-1.2-.1-2.4.7-3 .7-.6 0-1.6-.7-2.6-.7-1.4 0-2.6.8-3.3 2-1.4 2.5-.4 6.1 1 8.1.7 1 1.5 2.1 2.6 2 1 0 1.4-.7 2.7-.7s1.6.7 2.7.7c1.1 0 1.8-1 2.5-2 .8-1.1 1.1-2.2 1.1-2.3-.1 0-2.5-.9-2.5-3.1zm-2-5.9c.6-.7 1-1.7.9-2.7-.9 0-1.9.6-2.5 1.3-.5.6-1 1.6-.9 2.6 1 .1 1.9-.5 2.5-1.2z"/></svg>',
  ),
  TSLA: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#E31937"/><path fill="#fff" d="M16 24.2l2.2-11.8c2.1 0 2.8.2 2.9 1.1 0 0 1.4-.5 2.1-1.6-2.7-1.2-5.4-1.3-5.4-1.3L16 12.6l-1.8-2s-2.7.1-5.4 1.3c.7 1.1 2.1 1.6 2.1 1.6.1-.9.8-1.1 2.9-1.1L16 24.2z"/></svg>',
  ),
  QCOM: svgUri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="16" fill="#3253DC"/><path fill="#fff" d="M16 8.5a7.2 7.2 0 1 0 4.5 12.8l2.2 2.2 1.8-1.8-2.2-2.2A7.2 7.2 0 0 0 16 8.5zm0 2.6a4.6 4.6 0 1 1-3.1 8l1.6-1.6-1.7-1.7 1.7-1.7 3.3 3.3A4.6 4.6 0 0 0 16 11.1z"/></svg>',
  ),
}

// The trading venue and the underlying company identify these contracts.
// A bare ticker in the stock ledger may refer to a different asset in a crypto market.
const stocks: Record<string, string> = {
  ANT: anthropic,
  DELL: dell,
  SNDK: sndk,
  SPCX: spcx,
  SKHYNIX: skhynix,
  NVDA: BUILTIN_SVGS.NVDA,
  AAPL: BUILTIN_SVGS.AAPL,
  TSLA: BUILTIN_SVGS.TSLA,
  QCOM: BUILTIN_SVGS.QCOM,
}

const markets: Record<string, string> = {
  AKE: ake,
  B: b,
  BANK: bank,
  BEAT: beat,
  BTC: btc,
  DEXE: dexe,
  DOT: dot,
  ELSA: elsa,
  ESPORTS: esports,
  ETH: eth,
  EVAA: evaa,
  FIL: fil,
  GIGGLE: giggle,
  IRYS: irys,
  KAIA: kaia,
  MYX: myx,
  QNT: qnt,
  TRUMP: trump,
  USDT: usdt,
  USDC: BUILTIN_SVGS.USDC,
  SOL: BUILTIN_SVGS.SOL,
  DOGE: BUILTIN_SVGS.DOGE,
  BNB: BUILTIN_SVGS.BNB,
  GT: BUILTIN_SVGS.GT,
  XRP: BUILTIN_SVGS.XRP,
  NVDA: BUILTIN_SVGS.NVDA,
  AAPL: BUILTIN_SVGS.AAPL,
  TSLA: BUILTIN_SVGS.TSLA,
  QCOM: BUILTIN_SVGS.QCOM,
  VANRY: vanry,
  WLD: wld,
  ZEC: zec,
  DELLSTOCK: dell,
  SKHYNIX: skhynix,
  SNDK: sndk,
  SNDKSTOCK: sndk,
  SPCX: spcx,
  SPCXSTOCK: spcx,
  CL: crude,
  UKO: crude,
  USO: crude,
  XAG: silver,
  XAU: gold,
}

interface InstrumentMarkProps {
  instrument: string
  exchange?: string
  stock?: boolean
}

const FALLBACK_COLORS = ['#5e95f7', '#8b5cf6', '#2fc495', '#eab308', '#ec4899', '#3dc2df', '#6366f1']

function MonogramBadge({ base }: { base: string }) {
  const abbr = base.replace(/[^A-Z0-9]/g, '').slice(0, 2) || '?'
  const hash = Array.from(abbr).reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  const color = FALLBACK_COLORS[hash % FALLBACK_COLORS.length]
  return (
    <span
      className="instrument-mark-fallback"
      data-symbol={base}
      aria-hidden="true"
      style={{
        color,
        background: `${color}1f`,
        border: `1px solid ${color}4d`,
      }}
    >
      {abbr}
    </span>
  )
}

function parseInstrumentSymbol(raw: string, stock: boolean): { base: string; quote?: string } | null {
  const symbol = raw.trim().toUpperCase()
  if (!symbol) return null

  if (stock) {
    const stockMatch = /^([A-Z0-9]{1,12}(?:\.[A-Z0-9]{1,3})?)$/.exec(symbol)
    if (!stockMatch) return null
    const rawTicker = stockMatch[1]
    if (!Object.hasOwn(stocks, rawTicker) && rawTicker.length >= 4 && rawTicker.endsWith('X')) {
      const stripped = rawTicker.slice(0, -1)
      if (Object.hasOwn(stocks, stripped)) return { base: stripped }
    }
    return { base: rawTicker }
  }

  const sepMatch = /^([A-Z0-9\u3400-\u9fff]{1,32})[_/-]([A-Z0-9]{2,12})$/.exec(symbol)
  if (sepMatch) {
    return { base: sepMatch[1], quote: sepMatch[2] }
  }

  const plainMatch = /^([A-Z0-9\u3400-\u9fff]{1,32})$/.exec(symbol)
  if (!plainMatch) return null
  if (Object.hasOwn(markets, symbol)) {
    return { base: symbol }
  }

  const quoteSuffix = /^([A-Z0-9\u3400-\u9fff]{2,20}?)(USDT|USDC|FDUSD|BUSD|PERP|USD)$/.exec(symbol)
  if (quoteSuffix) {
    let base = quoteSuffix[1]
    if (!Object.hasOwn(markets, base) && base.length >= 4 && base.endsWith('X')) {
      const stripped = base.slice(0, -1)
      if (Object.hasOwn(markets, stripped)) base = stripped
    }
    return { base, quote: quoteSuffix[2] }
  }

  if (symbol.length >= 4 && symbol.endsWith('X')) {
    const stripped = symbol.slice(0, -1)
    if (Object.hasOwn(markets, stripped)) return { base: stripped }
  }

  return { base: symbol }
}

export function InstrumentMark({ instrument, exchange, stock = false }: InstrumentMarkProps) {
  const [failed, setFailed] = useState(false)
  const parsed = parseInstrumentSymbol(instrument, stock)
  if (!parsed) return null

  const { base, quote } = parsed
  const icons = stock && base !== 'USDT' ? stocks : markets
  if (Object.hasOwn(icons, base)) {
    return <img className="instrument-mark" src={icons[base]} data-symbol={base} alt="" draggable={false} />
  }

  if (failed || !exchange || (!stock && !quote)) {
    return <MonogramBadge base={base} />
  }
  const listing = stock ? base : `${base}_${quote}`
  const url = `/api/v1/instrument-logos/${encodeURIComponent(exchange)}/${encodeURIComponent(listing)}.png`
  return (
    <img
      key={url}
      className="instrument-mark"
      src={url}
      data-symbol={base}
      alt=""
      draggable={false}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}
