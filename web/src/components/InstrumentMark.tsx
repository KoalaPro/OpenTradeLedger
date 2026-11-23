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

// The trading venue and the underlying company identify these contracts.
// A bare ticker in the stock ledger may refer to a different asset in a crypto market.
const stocks: Record<string, string> = {
  ANT: anthropic,
  DELL: dell,
  SNDK: sndk,
  SPCX: spcx,
  SKHYNIX: skhynix,
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

const FALLBACK_COLORS = ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ec4899', '#06b6d4', '#6366f1']

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
        background: `${color}22`,
        border: `1px solid ${color}55`,
      }}
    >
      {abbr}
    </span>
  )
}

export function InstrumentMark({ instrument, exchange, stock = false }: InstrumentMarkProps) {
  const [failed, setFailed] = useState(false)
  const symbol = instrument.trim().toUpperCase()
  const match = stock
    ? /^([A-Z0-9]{1,12}(?:\.[A-Z0-9]{1,3})?)$/.exec(symbol)
    : /^([A-Z0-9\u3400-\u9fff]{1,32})(?:[_/-]([A-Z0-9]{2,12}))?$/.exec(symbol)
  if (!match) return null

  const base = match[1]
  const icons = stock && base !== 'USDT' ? stocks : markets
  if (Object.hasOwn(icons, base)) {
    return <img className="instrument-mark" src={icons[base]} data-symbol={base} alt="" draggable={false} />
  }

  if (failed || !exchange || (!stock && !match[2])) {
    return <MonogramBadge base={base} />
  }
  const listing = stock ? base : `${base}_${match[2]}`
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
