# Claude usage LCD: the contract between the three parts

```
Claude Code (desktop app or CLI, on the Windows PC or Mac)
  └─ mod "usage-lcd"  ──writes──▶  usage-lcd.json   (1. snapshot file)
                                        │ polled
                                   bridge (Python)  ──USB serial──▶  Arduino UNO/Mega + 16x2 I2C LCD
                                                    (2. serial protocol)        (3. screen layout)
```

Each part may be changed on its own as long as it keeps this contract.
The schema numbers below change only when a change breaks a part built to
the previous one.

## 1. Snapshot file (mod → bridge)

### Where it lives

Both the mod and the bridge resolve the path the same way, first match wins:

1. `USAGE_LCD_FILE` environment variable: the full path of the file.
2. `CLAUDE_CONFIG_DIR` environment variable: `<CLAUDE_CONFIG_DIR>/usage-lcd.json`.
3. `USERPROFILE` environment variable (Windows): `<USERPROFILE>/.claude/usage-lcd.json`.
4. `HOME` environment variable (macOS, Linux): `<HOME>/.claude/usage-lcd.json`. On macOS the
   desktop app and launchd set only `HOME`, so the file is `~/.claude/usage-lcd.json`.

The bridge also accepts `--file <path>`, which overrides all of the above.
A variable that is set but empty counts as unset. When joining, use `\` if
the base path contains a `\`, else `/`, and do not double a separator the
base already ends with.

### Contents (schema 1)

UTF-8 JSON, one object:

```json
{
  "schema": 1,
  "writtenAt": "2026-10-12T18:03:12.345Z",
  "source": "usage-lcd mod 0.1.0",
  "windows": [
    { "kind": "five_hour", "percentUsed": 23.5, "resetsAt": "2026-10-12T21:00:00.000Z" },
    { "kind": "seven_day", "percentUsed": 59,   "resetsAt": "2026-10-16T09:00:00.000Z" }
  ]
}
```

- `windows` mirrors the engine's `SessionRateLimit[]` exactly as reported:
  `kind` is `five_hour`, `seven_day` or anything else (e.g. a gateway's
  `spend_limit`); `percentUsed` is 0–100 with at most one decimal (may exceed
  100 on a spend limit); `resetsAt` is an ISO 8601 timestamp and may be absent
  (one without a UTC offset is read as local time).
- The mod writes the file only when it has at least one window. It never
  writes an empty `windows` list, so a session that has no reading yet can
  never wipe the numbers another session wrote.
- The mod writes the whole file in one call. Readers must tolerate a file
  that is missing, empty, half written or not valid JSON: keep the last good
  snapshot and try again on the next poll.
- Unknown fields are ignored by readers. A reader that sees a `schema` other
  than 1 treats the file as having no data.

## 2. Serial protocol (bridge ↔ Arduino)

- 9600 baud, 8N1, ASCII. Each message is one line ending in `\n`; a receiver
  ignores `\r`. Fields are separated by `|`. A line is at most 63 characters;
  a longer one is discarded whole.
- Opening the port resets an UNO or Mega (DTR). The Arduino announces itself
  when it is ready, so the bridge never has to guess a delay.

### PC → Arduino

| Line | Meaning |
| --- | --- |
| `U|<l5>|<r5>|<l7>|<r7>` | Usage. `l` = percent **left** in tenths, integer 0–1000, or `-1` unknown. `r` = whole seconds until that window resets, integer 0–2147483647, or `-1` unknown. `5` = the `five_hour` window, `7` = the `seven_day` window. Example: `U|765|4980|410|266400`. |
| `W` | The bridge is running but has no usage data yet (no file, unreadable file, or neither window present). |
| `?` | Query. The Arduino answers with its hello line. |

The bridge sends the current line as soon as it changes, and again every
10 seconds as a heartbeat. Since `r` counts down, a `U` line changes, and so
goes out, about once a second.

### Arduino → PC

| Line | Meaning |
| --- | --- |
| `R|usage-lcd|1` | Hello: firmware name and protocol version. Sent once after boot (LCD ready) and in reply to every `?`. |
| `E|<reason>` | Something went wrong: `bad-line` (a line it could not parse), `too-long` (a line over 63 characters), `no-lcd` (no LCD answered on I2C; repeated every 5 s). |

### Bridge: snapshot → `U` line

For each of `five_hour` and `seven_day`, at time `now`:

- Window absent from the snapshot → `l = -1`, `r = -1`.
- `resetsAt` present and `resetsAt <= now` → the window has reset since the
  reading: `l = 1000`, `r = -1`.
- Otherwise `l = clamp(round((100 - percentUsed) * 10), 0, 1000)` and
  `r = min(2147483647, max(0, floor((resetsAt - now) in seconds)))`, or
  `r = -1` when `resetsAt` is absent. Here and below, `round` means to the
  nearest integer with halves rounded up.
- Neither window present (or no usable snapshot) → send `W` instead of `U`.

### Bridge: finding the board

- `--port COMx` (macOS: `--port /dev/cu.usbserial-110`) uses that port only.
- Otherwise it probes serial ports, those with a known Arduino or USB-serial
  vendor id first (`0x2341`, `0x2A03` Arduino; `0x1A86` CH340; `0x0403` FTDI;
  `0x10C4` CP210x), then the rest, skipping Windows' serial-over-Bluetooth
  links (hardware id `BTHENUM…`), whose open can block. Probing a port = open it, wait up to 4 s
  for `R|usage-lcd|`, and if nothing came send `?` and wait 1.5 s more.
- On macOS it probes `/dev/cu.*` ports only, never their `/dev/tty.*` twins (whose open can
  wait for a carrier), and never a port that is not on USB: `/dev/cu.Bluetooth-Incoming-Port`,
  `/dev/cu.debug-console` and the port of each paired Bluetooth device. The macOS USB serial
  names come first, before the vendor id order: `/dev/cu.usbmodem*` (original UNO and Mega),
  `/dev/cu.usbserial*` (CH340 with Apple's driver, FTDI), `/dev/cu.wchusbserial*` (CH340 with
  WCH's driver), `/dev/cu.SLAB_USBtoUART*` (CP210x).
- On macOS and Linux the bridge also sets `TIOCEXCL` on the open port, so a program that
  opens it later (the Arduino IDE's upload or Serial Monitor) gets "Resource busy" instead of
  sharing it. This works one way only: avrdude neither locks nor sets `TIOCEXCL`, so a bridge
  that is still looking for its board can open the port during an upload. Stop the bridge
  before uploading.
- A port that is busy (another program holds it, e.g. the Arduino IDE's
  Serial Monitor or an upload) or vanishes (USB unplugged) is not fatal: the
  bridge logs it and tries again every 5 s.

## 3. Screen (Arduino, 16 × 2 characters)

Rows: row 0 is the `five_hour` window, labelled `5h`; row 1 is the
`seven_day` window, labelled `7d`.

### Bars page (shown 8 s)

```
col: 0123456789012345
     5h ▮▮▮▮▮▮▮▯▯ 72%
     7d ▮▮▮▮▯▯▯▯▯ 41%
```

- Columns 0–1: label. Column 2: a space, or `!` when `0 < l <= 100` (10 % or
  less left).
- Columns 3–11: a bar of 9 cells, 5 pixel columns each = 45 steps. Filled
  steps = `round(l * 45 / 1000)` (so `l = 100` gives 5), but at least 1 when `l > 0` and at most 44
  when `l < 1000`. Each cell is one of six custom characters (codes 0–5)
  showing 0–5 filled pixel columns, drawn with an outline so empty cells are
  still visible as part of the bar.
- Columns 12–15: percent left = `l / 10` rounded down, right-aligned with a
  `%`: `100%`, ` 72%`, `  0%`. Unknown (`l = -1`): ` --%` and an empty bar.

### Resets page (shown 4 s)

```
col: 0123456789012345
     5h resets  1h23m
     7d resets  3d04h
```

Columns 0–1 label, then ` resets ` (columns 2–9), then the time until reset
right-aligned in columns 10–15 (6 characters):

| Seconds left `s` | Shown |
| --- | --- |
| unknown (`-1`) | `--` |
| 0 | `now` |
| 1–59 | `45s` |
| 60–3599 | `23m05s` |
| 3600–86399 | `4h59m` (`23h59m`) |
| 86400 or more | `3d04h`; `99d+` from 100 days |

### Exhausted window (`l = 0`)

On both pages that row reads `5h LIMIT` followed by the reset time
right-aligned in columns 10–15, e.g. `5h LIMIT   1h23m`.

### Countdown on the Arduino

After a `U` line the Arduino counts each `r` down by itself with `millis()`
(wrap-safe unsigned arithmetic), so the resets page stays right between
messages. When a countdown from `r > 0` reaches 0 the window has reset: the Arduino
shows it as `l = 1000`, `r = -1` until the next line, which the bridge computes
the same way. A line that arrives with `r = 0` shows `now` until the next line.
A value the Arduino cannot parse (out of range, junk, wrong field count) is
answered with `E|bad-line` and leaves the screen as it was.

### Status screens

| When | Row 0 | Row 1 |
| --- | --- | --- |
| Booted, no line yet | `Claude usage LCD` | `waiting for PC..` |
| Last line was `W` | `Claude usage` | `waiting for data` |
| Had a line, none valid for 35 s | `PC link lost` | `start the bridge` |

Cells are rewritten only when they change, so the screen never flickers.
