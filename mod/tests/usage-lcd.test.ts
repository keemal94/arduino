import type { On, SessionRateLimit } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import { resolvePath } from '../hooks/snapshot'

const NOW = Date.parse('2026-10-12T18:03:12.345Z')
const PROFILE = 'C:\\Users\\Some Name'
const FILE = 'C:\\Users\\Some Name\\.claude\\usage-lcd.json'
const CONTEXT = { window: 200000 }
const TWO: SessionRateLimit[] = [
  { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-12T21:00:00.000Z' },
  { kind: 'seven_day', percentUsed: 59, resetsAt: '2026-10-16T09:00:00.000Z' },
]

// The engine beneath the mod: env and clock from memory, writes and log lines recorded.
function engine(on: On, env: Record<string, string> = { USERPROFILE: PROFILE }) {
  mock.env(on, env)
  mock.clock(on, { now: NOW })
  const world = {
    writes: [] as { path: string; text: string }[],
    logs: [] as { text: string; to: string }[],
    failWrites: false,
    rateLimitsAtStart: [] as SessionRateLimit[],
  }
  on('fs.write', ($, e) => {
    if (world.failWrites) return { deny: 'EBUSY: resource busy or locked' }
    world.writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('ui.log', ($, e) => {
    world.logs.push({ text: e.text, to: e.to })
    return { value: undefined }
  })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.measure', ($, e) => ({ changed: [...e.changed] }))
  on('session.usage', () => ({
    value: { startedAt: NOW, context: CONTEXT, rateLimits: world.rateLimitsAtStart },
  }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  return world
}

const measure = ($: Engine, rateLimits: SessionRateLimit[]) =>
  $.session.measure({ context: CONTEXT, rateLimits, changed: ['rateLimits'] })

const start = ($: Engine) => $.session.start({ cwd: '/work', surface: null, isInteractive: false })

const usageLcd = ($: Engine) =>
  $.command.run({
    command: 'usage-lcd',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

// Off Windows the engine resolves a Windows path against the working directory,
// so a test checks the path the mod gave as the tail of the one written.
const paths = (world: { writes: { path: string }[] }) =>
  world.writes.map(w => (w.path.endsWith(FILE) ? FILE : w.path))

describe('file path', () => {
  const all = {
    USAGE_LCD_FILE: 'D:\\LCD files\\usage.json',
    CLAUDE_CONFIG_DIR: 'C:\\Users\\Some Name\\claude config',
    USERPROFILE: PROFILE,
    HOME: '/home/some one',
  }

  test('USAGE_LCD_FILE wins, as given', () => {
    expect(resolvePath(all)).toBe('D:\\LCD files\\usage.json')
  })

  test('then CLAUDE_CONFIG_DIR, joined with a backslash', () => {
    expect(resolvePath({ ...all, USAGE_LCD_FILE: undefined })).toBe(
      'C:\\Users\\Some Name\\claude config\\usage-lcd.json',
    )
  })

  test('then USERPROFILE, before HOME', () => {
    expect(resolvePath({ USERPROFILE: PROFILE, HOME: '/home/some one' })).toBe(FILE)
  })

  test('then HOME, joined with a slash', () => {
    expect(resolvePath({ HOME: '/home/some one' })).toBe('/home/some one/.claude/usage-lcd.json')
  })

  test('empty counts as unset, a trailing separator is not doubled, none is undefined', () => {
    expect(resolvePath({ USAGE_LCD_FILE: '', CLAUDE_CONFIG_DIR: '', USERPROFILE: `${PROFILE}\\` })).toBe(FILE)
    expect(resolvePath({ HOME: '/' })).toBe('/.claude/usage-lcd.json')
    expect(resolvePath({})).toBeUndefined()
  })

  test('the mod reads the environment in that order', async ($, on) => {
    const world = engine(on, { CLAUDE_CONFIG_DIR: 'C:\\Users\\Some Name\\claude config', HOME: '/h' })
    await measure($, TWO)
    expect(world.writes).toHaveLength(1)
    expect(world.writes[0]?.path).toEndWith('C:\\Users\\Some Name\\claude config\\usage-lcd.json')
  })
})

describe('snapshot', () => {
  test("a two-window measure writes the contract's JSON in one call", async ($, on) => {
    const world = engine(on)
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(1)
    const [write] = world.writes
    expect(paths(world)).toEqual([FILE])
    const expected = {
      schema: 1,
      writtenAt: '2026-10-12T18:03:12.345Z',
      source: 'usage-lcd mod 0.1.0',
      windows: [
        { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-12T21:00:00.000Z' },
        { kind: 'seven_day', percentUsed: 59, resetsAt: '2026-10-16T09:00:00.000Z' },
      ],
    }
    expect(write?.text).toBe(`${JSON.stringify(expected, null, 2)}\n`)
    expect(JSON.parse(write?.text ?? '')).toStrictEqual(expected)
  })

  test('empty rateLimits writes nothing, before or after a reading', async ($, on) => {
    const world = engine(on)
    await measure($, [])
    expect(world.writes).toHaveLength(0)
    await measure($, TWO)
    await measure($, [])
    expect(world.writes).toHaveLength(1)
  })

  test('an identical repeat writes nothing; a moved window writes again', async ($, on) => {
    const world = engine(on)
    await measure($, TWO)
    await measure($, [...TWO])
    expect(world.writes).toHaveLength(1)
    const moved: SessionRateLimit[] = [{ ...TWO[0]!, percentUsed: 24 }, { kind: 'spend_limit', percentUsed: 101.5 }]
    await measure($, moved)
    expect(world.writes).toHaveLength(2)
    expect(JSON.parse(world.writes[1]?.text ?? '').windows).toStrictEqual([
      { kind: 'five_hour', percentUsed: 24, resetsAt: '2026-10-12T21:00:00.000Z' },
      { kind: 'spend_limit', percentUsed: 101.5 },
    ])
  })

  test('a failing write keeps the measure chain, logs to debug, and is retried', async ($, on) => {
    const world = engine(on)
    world.failWrites = true
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(0)
    expect(world.logs).toHaveLength(1)
    expect(world.logs[0]?.to).toBe('debug')
    expect(world.logs[0]?.text).toMatch(/EBUSY/)
    world.failWrites = false
    await measure($, TWO)
    expect(world.writes).toHaveLength(1)
  })

  test('no path at all writes nothing and keeps the chain', async ($, on) => {
    const world = engine(on, {})
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(0)
    expect(world.logs[0]?.to).toBe('debug')
  })

  test('session start writes a reading the session already has', async ($, on) => {
    const world = engine(on)
    world.rateLimitsAtStart = TWO
    expect(await start($)).toEqual({ cwd: '/work' })
    expect(paths(world)).toEqual([FILE])
    await measure($, TWO)
    expect(world.writes).toHaveLength(1)
  })

  test('session start with no reading writes nothing', async ($, on) => {
    const world = engine(on)
    await start($)
    expect(world.writes).toHaveLength(0)
  })
})

describe('/usage-lcd', () => {
  test('before any reading it says where and why nothing yet', async ($, on) => {
    engine(on)
    await start($)
    const { text } = await usageLcd($)
    expect(text).toBe(
      `Usage file: ${FILE}\n` +
        "Nothing written yet: the numbers appear after Claude's first reply in a session, and only on a Pro or Max plan.",
    )
  })

  test('after a reading it shows percent left and reset time per window', async ($, on) => {
    engine(on)
    await start($)
    await measure($, TWO)
    const { text } = await usageLcd($)
    expect(text).toBe(
      [
        `Usage file: ${FILE}`,
        'Last written 2026-10-12T18:03:12.345Z:',
        '  5h: 76.5% left, resets in 2h 56m (2026-10-12T21:00:00.000Z)',
        '  7d: 41% left, resets in 3d 14h (2026-10-16T09:00:00.000Z)',
      ].join('\n'),
    )
  })
})
