import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AuthGate } from '../components/AuthGate'
import { en } from '../i18n/en'
import { zhCN } from '../i18n/zh-CN'

interface MinimalNodeFs {
  readFileSync: (filePath: string, encoding: string) => string
  readdirSync: (dirPath: string, opts: { withFileTypes: true }) => Array<{
    name: string
    isDirectory: () => boolean
  }>
}

function collectSourceFiles(fs: MinimalNodeFs, dir: string): string[] {
  const out: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__tests__') continue
    const full = `${dir}/${entry.name}`
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(fs, full))
    } else if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) {
      out.push(full)
    }
  }
  return out
}

afterEach(() => {
  cleanup()
})

describe('Cross-Module CSS, i18n & Component Isolation Contracts', () => {
  it('ensures every otl-* CSS class referenced in TSX/TS exists in theme.css (zero orphaned classes)', async () => {
    const fsModName = 'node:fs'
    const nodeFs = (await import(/* @vite-ignore */ fsModName)) as MinimalNodeFs
    const themeCssRaw = nodeFs.readFileSync('src/styles/theme.css', 'utf-8')

    const definedClasses = new Set<string>()
    const cssRe = /\.(otl-[a-zA-Z0-9_-]+)/g
    let cssMatch: RegExpExecArray | null = cssRe.exec(themeCssRaw)
    while (cssMatch !== null) {
      definedClasses.add(cssMatch[1])
      cssMatch = cssRe.exec(themeCssRaw)
    }
    expect(definedClasses.size).toBeGreaterThan(20)

    const nonCssIds = new Set(['otl-root'])
    const missing: Record<string, string[]> = {}

    for (const filePath of collectSourceFiles(nodeFs, 'src')) {
      const content = nodeFs.readFileSync(filePath, 'utf-8')
      const useRe = /\b(otl-[a-zA-Z0-9_-]+)\b/g
      let m: RegExpExecArray | null = useRe.exec(content)
      while (m !== null) {
        const cls = m[1]
        if (!definedClasses.has(cls) && !nonCssIds.has(cls)) {
          missing[cls] = missing[cls] || []
          if (!missing[cls].includes(filePath)) missing[cls].push(filePath)
        }
        m = useRe.exec(content)
      }
    }

    expect(missing).toEqual({})
  })

  it('enforces 1:1 bidirectional key parity and non-empty strings between zh-CN and en i18n modules', () => {
    const zhKeys = Object.keys(zhCN).sort()
    const enKeys = Object.keys(en).sort()
    expect(zhKeys).toEqual(enKeys)

    for (const k of zhKeys) {
      expect(String((zhCN as Record<string, string>)[k]).trim().length).toBeGreaterThan(0)
      expect(String((en as Record<string, string>)[k]).trim().length).toBeGreaterThan(0)
    }
  })

  it('uses AnimatedCollapse in AuthGate forgot-password disclosure without orphaned classes', () => {
    render(
      <AuthGate
        auth={{ initialized: true, enabled: true, authenticated: false, require_setup: false }}
        locale="zh-CN"
        onToggleLocale={() => {}}
        onUnlocked={() => {}}
      />,
    )

    fireEvent.click(screen.getByText('忘记密码？'))
    expect(screen.getByText(/--reset-password/)).toBeTruthy()
    expect(document.querySelector('.otl-collapse-grid')).toBeTruthy()
  })
})
