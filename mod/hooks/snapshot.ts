// Pure logic of the snapshot file (docs/PROTOCOL.md section 1): no `$` here.
import type { SessionRateLimit } from 'claude-code'

// Keep in step with .claude-plugin/plugin.json.
export const VERSION = '0.1.0'

export type Env = {
  USAGE_LCD_FILE?: string
  CLAUDE_CONFIG_DIR?: string
  USERPROFILE?: string
  HOME?: string
}

export type Window = { kind: string; percentUsed: number; resetsAt?: string }

export type Snapshot = {
  schema: 1
  writtenAt: string
  source: string
  windows: Window[]
}

// First variable set wins; an empty one counts as unset.
export function resolvePath(env: Env): string | undefined {
  if (env.USAGE_LCD_FILE) return env.USAGE_LCD_FILE
  if (env.CLAUDE_CONFIG_DIR) return join(env.CLAUDE_CONFIG_DIR, 'usage-lcd.json')
  const home = env.USERPROFILE || env.HOME
  return home ? join(home, '.claude', 'usage-lcd.json') : undefined
}

// `\` when the base holds one, else `/`; a trailing separator on the base is not doubled.
export function join(base: string, ...parts: string[]): string {
  const sep = base.includes('\\') ? '\\' : '/'
  return [base.replace(/[\\/]+$/, ''), ...parts].join(sep)
}

// The engine's windows as reported, `resetsAt` left out when absent.
export function toWindows(rateLimits: readonly SessionRateLimit[]): Window[] {
  return rateLimits.map(({ kind, percentUsed, resetsAt }) =>
    resetsAt === undefined ? { kind, percentUsed } : { kind, percentUsed, resetsAt },
  )
}

export function buildSnapshot(windows: Window[], nowMs: number): Snapshot {
  return {
    schema: 1,
    writtenAt: new Date(nowMs).toISOString(),
    source: `usage-lcd mod ${VERSION}`,
    windows,
  }
}

export const serialize = (snapshot: Snapshot): string => `${JSON.stringify(snapshot, null, 2)}\n`

const LABELS: Record<string, string> = { five_hour: '5h', seven_day: '7d' }

function resets(resetsAt: string | undefined, nowMs: number): string {
  if (resetsAt === undefined) return 'reset time unknown'
  const s = Math.floor((Date.parse(resetsAt) - nowMs) / 1000)
  if (Number.isNaN(s)) return `resets ${resetsAt}`
  if (s <= 0) return `reset at ${resetsAt} (passed)`
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const left = d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`
  return `resets in ${left} (${resetsAt})`
}

// The file's snapshot, or undefined when it holds none this mod could have written.
export function parseSnapshot(text: string): Snapshot | undefined {
  const isWindow = (w: any) =>
    typeof w?.kind === 'string' &&
    typeof w.percentUsed === 'number' &&
    (w.resetsAt === undefined || typeof w.resetsAt === 'string')
  try {
    const s = JSON.parse(text)
    return s?.schema === 1 && typeof s.writtenAt === 'string' && Array.isArray(s.windows) && s.windows.every(isWindow)
      ? s
      : undefined
  } catch {
    return undefined
  }
}

// The /usage-lcd answer: where the file is, what it holds (`text` undefined: unreadable) and the last failure.
export function report(path: string | undefined, text: string | undefined, lastError: string | undefined, nowMs: number): string {
  const lines: string[] = []
  const snapshot = text === undefined ? undefined : parseSnapshot(text)
  if (path === undefined) {
    lines.push('Usage file: none, as USAGE_LCD_FILE, CLAUDE_CONFIG_DIR, USERPROFILE and HOME are all unset.')
  } else if (text === undefined) {
    lines.push(
      `Usage file: ${path} (missing or unreadable)`,
      "It is written after Claude's first reply in a session, and only when Claude Code is signed in with a Claude subscription, not an API key.",
    )
  } else if (snapshot === undefined) {
    lines.push(`Usage file: ${path} (holds no readable snapshot)`)
  } else {
    lines.push(`Usage file: ${path}`, `Last written ${snapshot.writtenAt}:`)
    for (const w of snapshot.windows) {
      const left = Math.max(0, Math.round((100 - w.percentUsed) * 10) / 10)
      lines.push(`  ${LABELS[w.kind] ?? w.kind}: ${left}% left, ${resets(w.resetsAt, nowMs)}`)
    }
  }
  if (lastError !== undefined) lines.push(`Last error: ${lastError}`)
  return lines.join('\n')
}
