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
const TWO_TEXT = [
  '  5h: 76.5% left, resets in 2h 56m (2026-10-12T21:00:00.000Z)',
  '  7d: 41% left, resets in 3d 14h (2026-10-16T09:00:00.000Z)',
]
// What TWO is written as: byte for byte tests/fixtures/normal.json, which the bridge's
// tests read (this runner cannot); tests/e2e checks that the two stay equal.
const NORMAL_JSON = `{
  "schema": 1,
  "writtenAt": "2026-10-12T18:03:12.345Z",
  "source": "usage-lcd mod 0.1.0",
  "windows": [
    {
      "kind": "five_hour",
      "percentUsed": 23.5,
      "resetsAt": "2026-10-12T21:00:00.000Z"
    },
    {
      "kind": "seven_day",
      "percentUsed": 59,
      "resetsAt": "2026-10-16T09:00:00.000Z"
    }
  ]
}
`

// Off Windows the engine resolves a Windows path against the working directory
// before a hook sees it: keep the part from the drive letter on.
const asGiven = (path: string) => path.replace(/^.*?(?=[A-Za-z]:\\)/, '')

// The engine beneath the mod: env, files and clock from memory, writes and log lines recorded.
// The default env is the desktop app's on Windows: USERPROFILE set, HOME unset.
function engine(on: On, env: Record<string, string> = { USERPROFILE: PROFILE }) {
  mock.clock(on, { now: NOW })
  const world = {
    env,
    files: new Map<string, string>(),
    writes: [] as { path: string; text: string }[],
    logs: [] as { text: string; to: string }[],
    commands: [] as { name: string; immediate?: true }[],
    reached: { start: 0, measure: 0 }, // how often the chain got down to the engine
    failWrites: false,
    failUsage: false,
    failRegister: false,
    rateLimitsAtStart: [] as SessionRateLimit[],
  }
  on('env.get', ($, e) => ({ value: world.env[e.name] }))
  on('fs.write', ($, e) => {
    if (world.failWrites) return { deny: 'EBUSY: resource busy or locked' }
    world.writes.push({ path: asGiven(e.path), text: e.text })
    world.files.set(asGiven(e.path), e.text)
    return { value: undefined }
  })
  on('fs.read', ($, e) => {
    const text = world.files.get(asGiven(e.path))
    return text === undefined ? { deny: 'ENOENT: no such file or directory' } : { value: text }
  })
  on('ui.log', ($, e) => {
    world.logs.push({ text: e.text, to: e.to })
    return { value: undefined }
  })
  on('session.start', ($, e) => (world.reached.start++, { cwd: e.cwd }))
  on('session.measure', ($, e) => (world.reached.measure++, { changed: [...e.changed] }))
  on('session.usage', () =>
    world.failUsage
      ? { deny: 'usage unavailable' }
      : { value: { startedAt: NOW, context: CONTEXT, rateLimits: world.rateLimitsAtStart } },
  )
  on('command.register', ($, e) => {
    if (world.failRegister) return { deny: 'refused' }
    world.commands.push({ name: e.name, immediate: e.immediate })
    return { value: { command: e.name } }
  })
  return world
}

const measure = ($: Engine, rateLimits: SessionRateLimit[], changed: ('rateLimits' | 'context')[] = ['rateLimits']) =>
  $.session.measure({ context: CONTEXT, rateLimits, changed })

const start = ($: Engine) => $.session.start({ cwd: '/work', surface: null, isInteractive: false })

const usageLcd = async ($: Engine) =>
  (
    await $.command.run({
      command: 'usage-lcd',
      args: '',
      origin: { kind: 'composer' },
      presentation: { isFullscreen: false, columns: 80 },
    })
  ).text

const paths = (world: { writes: { path: string }[] }) => world.writes.map(w => w.path)

// Nothing the mod logs may reach the person's transcript.
const debugOnly = (world: { logs: { to: string }[] }) => world.logs.every(l => l.to === 'debug')

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
    expect(resolvePath({ USERPROFILE: 'C:\\' })).toBe('C:\\.claude\\usage-lcd.json')
    expect(resolvePath({ CLAUDE_CONFIG_DIR: 'C:/Users/X/.claude/' })).toBe('C:/Users/X/.claude/usage-lcd.json')
    expect(resolvePath({ HOME: '/' })).toBe('/.claude/usage-lcd.json')
    expect(resolvePath({})).toBeUndefined()
  })

  test('the desktop app on macOS: HOME only, joined with a slash', async ($, on) => {
    const macFile = '/Users/Some Name/.claude/usage-lcd.json'
    const world = engine(on, { HOME: '/Users/Some Name' })
    await start($)
    await measure($, TWO)
    expect(paths(world)).toEqual([macFile])
    expect(world.writes[0]?.text).toBe(NORMAL_JSON)
    expect(await usageLcd($)).toBe(
      [`Usage file: ${macFile}`, 'Last written 2026-10-12T18:03:12.345Z:', ...TWO_TEXT].join('\n'),
    )
  })

  test('the mod reads the environment in that order, and follows a change of path', async ($, on) => {
    const world = engine(on, { ...all })
    const steps: [Partial<Record<keyof typeof all, string>>, string][] = [
      [{}, 'D:\\LCD files\\usage.json'],
      [{ USAGE_LCD_FILE: '' }, 'C:\\Users\\Some Name\\claude config\\usage-lcd.json'],
      [{ CLAUDE_CONFIG_DIR: '' }, FILE],
      [{ USERPROFILE: '' }, '/home/some one/.claude/usage-lcd.json'],
    ]
    for (const [change, path] of steps) {
      Object.assign(world.env, change)
      await measure($, TWO)
      expect(world.writes.at(-1)?.path).toBe(path)
    }
    // Same windows each time: the new path alone made each write.
    expect(world.writes).toHaveLength(steps.length)
  })
})

describe('snapshot', () => {
  test("a two-window measure writes the contract's JSON in one call", async ($, on) => {
    const world = engine(on)
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(1)
    const [write] = world.writes
    expect(paths(world)).toEqual([FILE])
    expect(write?.text).toBe(NORMAL_JSON)
  })

  test('empty rateLimits writes nothing, before or after a reading', async ($, on) => {
    const world = engine(on)
    await measure($, [])
    expect(world.writes).toHaveLength(0)
    await measure($, TWO)
    await measure($, [])
    expect(world.writes).toHaveLength(1)
    expect(world.reached.measure).toBe(3)
  })

  test('an identical repeat writes nothing; a moved window writes again', async ($, on) => {
    const world = engine(on)
    await measure($, TWO)
    await measure($, [...TWO], ['context'])
    expect(world.writes).toHaveLength(1)
    const moved: SessionRateLimit[] = [{ ...TWO[0]!, percentUsed: 24 }, { kind: 'spend_limit', percentUsed: 101.5 }]
    await measure($, moved)
    expect(world.writes).toHaveLength(2)
    expect(JSON.parse(world.writes[1]?.text ?? '').windows).toStrictEqual([
      { kind: 'five_hour', percentUsed: 24, resetsAt: '2026-10-12T21:00:00.000Z' },
      { kind: 'spend_limit', percentUsed: 101.5 },
    ])
  })

  test('the same percent with a new reset time writes again', async ($, on) => {
    const world = engine(on)
    await measure($, TWO)
    const reset: SessionRateLimit[] = [{ ...TWO[0]!, resetsAt: '2026-10-13T02:00:00.000Z' }, TWO[1]!]
    await measure($, reset)
    expect(world.writes).toHaveLength(2)
    expect(JSON.parse(world.writes[1]?.text ?? '').windows[0].resetsAt).toBe('2026-10-13T02:00:00.000Z')
  })

  test('a measure that names only the context still writes a new reading', async ($, on) => {
    const world = engine(on)
    await measure($, TWO, ['context'])
    expect(paths(world)).toEqual([FILE])
  })

  test('a failing write keeps the measure chain, logs to debug only, and is retried', async ($, on) => {
    const world = engine(on)
    await start($)
    world.failWrites = true
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(0)
    expect(world.logs).toHaveLength(1)
    expect(debugOnly(world)).toBe(true)
    expect(world.logs[0]?.text).toMatch(/^usage file not updated: .*EBUSY/)
    expect(await usageLcd($)).toMatch(/\nLast error: usage file not updated: .*EBUSY/)
    world.failWrites = false
    await measure($, TWO)
    expect(world.writes).toHaveLength(1)
    expect(world.reached).toEqual({ start: 1, measure: 2 })
    expect(await usageLcd($)).not.toMatch(/Last error/)
  })

  test('no path at all writes nothing and keeps the chain', async ($, on) => {
    const world = engine(on, {})
    expect(await measure($, TWO)).toEqual({ changed: ['rateLimits'] })
    expect(world.writes).toHaveLength(0)
    expect(world.logs).toHaveLength(1)
    expect(debugOnly(world)).toBe(true)
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
    expect(world.logs).toHaveLength(0)
  })

  test('session start survives a failing usage read and a refused registration', async ($, on) => {
    const world = engine(on)
    world.failUsage = true
    world.failRegister = true
    expect(await start($)).toEqual({ cwd: '/work' })
    expect(world.reached.start).toBe(1)
    expect(world.logs.map(l => l.text)).toEqual([
      expect.stringMatching(/^\/usage-lcd not registered: .*refused/),
      expect.stringMatching(/^usage file not updated: .*usage unavailable/),
    ])
    expect(debugOnly(world)).toBe(true)
    await measure($, TWO)
    expect(paths(world)).toEqual([FILE])
  })
})

describe('/usage-lcd', () => {
  test('is registered at session start, runnable mid-turn', async ($, on) => {
    const world = engine(on)
    await start($)
    expect(world.commands).toEqual([{ name: 'usage-lcd', immediate: true }])
  })

  test('before any file it says where and why nothing yet', async ($, on) => {
    engine(on)
    await start($)
    expect(await usageLcd($)).toBe(
      `Usage file: ${FILE} (missing or unreadable)\n` +
        "It is written after Claude's first reply in a session, and only when Claude Code is signed in with a Claude subscription, not an API key.",
    )
  })

  test('after a reading it shows percent left and reset time per window', async ($, on) => {
    engine(on)
    await start($)
    await measure($, TWO)
    expect(await usageLcd($)).toBe(
      [`Usage file: ${FILE}`, 'Last written 2026-10-12T18:03:12.345Z:', ...TWO_TEXT].join('\n'),
    )
  })

  test('it shows a file another session wrote', async ($, on) => {
    const world = engine(on)
    world.files.set(
      FILE,
      JSON.stringify({
        schema: 1,
        writtenAt: '2026-10-12T17:00:00.000Z',
        source: 'usage-lcd mod 0.1.0',
        windows: [
          { kind: 'five_hour', percentUsed: 100, resetsAt: '2026-10-12T18:00:00.000Z' },
          { kind: 'spend_limit', percentUsed: 101.5 },
        ],
      }),
    )
    await start($)
    expect(await usageLcd($)).toBe(
      [
        `Usage file: ${FILE}`,
        'Last written 2026-10-12T17:00:00.000Z:',
        '  5h: 100% left, reset at 2026-10-12T18:00:00.000Z (passed)',
        '  spend_limit: 0% left, reset time unknown',
      ].join('\n'),
    )
  })

  test('a file that is not a snapshot is reported as such', async ($, on) => {
    const world = engine(on)
    await start($)
    for (const text of ['', '{"schema":1,"wri', '{"schema":2,"writtenAt":"x","windows":[]}', '{"schema":1,"writtenAt":"x","windows":[null]}']) {
      world.files.set(FILE, text)
      expect(await usageLcd($)).toBe(`Usage file: ${FILE} (holds no readable snapshot)`)
    }
  })

  test('with no path it says which variables it looked at', async ($, on) => {
    engine(on, {})
    await start($)
    expect(await usageLcd($)).toBe(
      'Usage file: none, as USAGE_LCD_FILE, CLAUDE_CONFIG_DIR, USERPROFILE and HOME are all unset.',
    )
  })
})
