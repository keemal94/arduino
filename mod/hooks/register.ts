// usage-lcd: writes the account's rate-limit windows to the snapshot file the
// bridge polls (docs/PROTOCOL.md section 1), and answers /usage-lcd.
import type { EngineInterface, Register, SessionRateLimit } from 'claude-code'

import { buildSnapshot, report, resolvePath, serialize, toWindows } from './snapshot'
import type { Snapshot } from './snapshot'

// What this module last wrote; a reload starts over, which costs one write.
let lastKey: string | undefined // its windows, as JSON
let last: Snapshot | undefined

async function filePath($: EngineInterface): Promise<string | undefined> {
  return resolvePath({
    USAGE_LCD_FILE: await $.env.get('USAGE_LCD_FILE'),
    CLAUDE_CONFIG_DIR: await $.env.get('CLAUDE_CONFIG_DIR'),
    USERPROFILE: await $.env.get('USERPROFILE'),
    HOME: await $.env.get('HOME'),
  })
}

// Never writes an empty list, nor the same windows twice in a row.
async function publish($: EngineInterface, rateLimits: readonly SessionRateLimit[]): Promise<void> {
  if (rateLimits.length === 0) return
  const windows = toWindows(rateLimits)
  const key = JSON.stringify(windows)
  if (key === lastKey) return
  const path = await filePath($)
  if (path === undefined) throw new Error('no path: USAGE_LCD_FILE, CLAUDE_CONFIG_DIR, USERPROFILE and HOME are unset')
  const snapshot = buildSnapshot(windows, await $.clock.now())
  await $.fs.write(path, serialize(snapshot))
  lastKey = key
  last = snapshot
}

// A failure here must never disturb the session: it goes to the debug log only.
async function guarded($: EngineInterface, work: () => Promise<unknown>): Promise<void> {
  try {
    await work()
  } catch (err) {
    $.ui.log(`usage file not updated: ${err instanceof Error ? err.message : String(err)}`, { to: 'debug' })
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await guarded($, () =>
      $.command.register({
        name: 'usage-lcd',
        description: 'Show where the usage LCD file is and the numbers last written to it',
      }),
    )
    const started = await next(e)
    // A resumed session may already have a reading.
    await guarded($, async () => publish($, (await $.session.usage()).rateLimits))
    return started
  })

  on('session.measure', async ($, e, next) => {
    const measured = await next(e)
    await guarded($, () => publish($, e.rateLimits))
    return measured
  })

  on('command.run', { command: 'usage-lcd' }, async $ => ({
    text: report(await filePath($), last, await $.clock.now()),
  }))
}
