// usage-lcd: writes the account's rate-limit windows to the snapshot file the
// bridge polls (docs/PROTOCOL.md section 1), and answers /usage-lcd.
import type { EngineInterface, Register, SessionRateLimit } from 'claude-code'

import { buildSnapshot, report, resolvePath, serialize, toWindows } from './snapshot'

// The path and windows this module last wrote, as JSON; a reload starts over, which costs one write.
let lastKey: string | undefined
// The last failure, shown by /usage-lcd until a write succeeds.
let lastError: string | undefined

async function filePath($: EngineInterface): Promise<string | undefined> {
  return resolvePath({
    USAGE_LCD_FILE: await $.env.get('USAGE_LCD_FILE'),
    CLAUDE_CONFIG_DIR: await $.env.get('CLAUDE_CONFIG_DIR'),
    USERPROFILE: await $.env.get('USERPROFILE'),
    HOME: await $.env.get('HOME'),
  })
}

// Never writes an empty list, nor the same windows to the same path twice in a row.
async function publish($: EngineInterface, rateLimits: readonly SessionRateLimit[]): Promise<void> {
  if (rateLimits.length === 0) return
  const path = await filePath($)
  if (path === undefined) throw new Error('no path: USAGE_LCD_FILE, CLAUDE_CONFIG_DIR, USERPROFILE and HOME are unset')
  const windows = toWindows(rateLimits)
  const key = JSON.stringify([path, windows])
  if (key === lastKey) return
  await $.fs.write(path, serialize(buildSnapshot(windows, await $.clock.now())))
  lastKey = key
  lastError = undefined
}

// A failure here must never disturb the session: it goes to the debug log and /usage-lcd only.
async function guarded($: EngineInterface, what: string, work: () => Promise<unknown>): Promise<void> {
  try {
    await work()
  } catch (err) {
    lastError = `${what}: ${err instanceof Error ? err.message : String(err)}`
    $.ui.log(lastError, { to: 'debug' })
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await guarded($, '/usage-lcd not registered', () =>
      $.command.register({
        name: 'usage-lcd',
        description: 'Show where the usage LCD file is and what it holds',
        immediate: true,
      }),
    )
    const started = await next(e)
    // The session may already hold a reading.
    await guarded($, 'usage file not updated', async () => publish($, (await $.session.usage()).rateLimits))
    return started
  })

  on('session.measure', async ($, e, next) => {
    const measured = await next(e)
    await guarded($, 'usage file not updated', () => publish($, e.rateLimits))
    return measured
  })

  // Reads the file itself, so it shows what the bridge sees, whichever session wrote it.
  on('command.run', { command: 'usage-lcd' }, async $ => {
    const path = await filePath($)
    const text = path === undefined ? undefined : await $.fs.read(path).catch(() => undefined)
    return { text: report(path, text, lastError, await $.clock.now()) }
  })
}
