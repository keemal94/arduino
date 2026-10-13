# Claude usage LCD

Shows how much of your Claude usage is left on a 16×2 LCD next to your PC. The top
row is the 5-hour window and the bottom row the 7-day window. Each row has a bar graph
and the percent **left**. Every 12 seconds the screen switches for 4 seconds to the
time until each window resets.

```
 Bars page, 8 s        Resets page, 4 s
┌────────────────┐    ┌────────────────┐
│5h ██████▊░░ 76%│    │5h resets  2h30m│
│7d ███▋░░░░░ 41%│    │7d resets  3d14h│
└────────────────┘    └────────────────┘
```

When a window is used up, its row says `LIMIT` and how long until it resets. A `!`
after the label means 10 % or less is left:

```
┌────────────────┐    ┌────────────────┐
│5h LIMIT  45m08s│    │5h LIMIT  45m00s│
│7d!▋░░░░░░░░  7%│    │7d resets 11h30m│
└────────────────┘    └────────────────┘
```

(These previews come from the firmware's own display code. On the real LCD the empty
part of the bar is drawn as an outlined tube.)

**How it works:** a small Claude Code mod (`mod\`) writes your usage to
`%USERPROFILE%\.claude\usage-lcd.json` each time Claude replies. A Python program, the
bridge (`bridge\`), reads that file and sends it over USB to the Arduino (`firmware\`),
which draws the screen. The details are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

**On a Mac?** The hardware and the firmware are the same. Follow [macOS](#macos) instead
of the Windows steps below.

## What you need

- An Arduino UNO or Mega 2560. Clones with a CH340 USB chip are fine. You also need a
  USB **data** cable; a charge-only cable will not work.
- A 16×2 LCD with an I2C backpack (PCF8574) soldered on, and 4 female-to-male jumper
  wires.
- A Windows PC with the Claude desktop app, signed in with a Claude subscription (such as
  **Pro or Max**), not an API key.

## 1. Get the files

1. Download this repository as a ZIP. On GitHub, click **Code → Download ZIP**.
2. Right-click the ZIP and choose **Properties**. If you see an **Unblock** box, tick it
   and click **OK**. This stops Windows from warning about the scripts later.
3. Extract the ZIP. Windows often puts a folder inside a folder of the same name (its
   name starts with `arduino-`): take the inner one, the folder that holds
   `README.md`, `bridge`, `firmware` and `mod`. Rename it to `claude-usage-lcd` and put it
   somewhere you will keep it, for example your Documents folder. The bridge and the mod run
   from this folder. If you move it later, repeat steps 5 and 6.

## 2. Wire the LCD

Unplug the USB cable first.

| LCD backpack pin | Arduino UNO | Arduino Mega 2560 |
| --- | --- | --- |
| GND | GND | GND |
| VCC | 5V | 5V |
| SDA | A4 | 20 (SDA) |
| SCL | A5 | 21 (SCL) |

On an UNO R3, the pins marked SDA and SCL next to AREF are the same signals as A4 and
A5, so you can use either. The firmware finds the LCD's I2C address (0x27, 0x3F or
another) by itself.

## 3. Upload the firmware

1. Install **Arduino IDE 2** from <https://www.arduino.cc/en/software> and start it.
2. Open **Tools → Manage Libraries…**, search for `LiquidCrystal I2C`, and install the one
   **by Frank de Brabander**. Other libraries with similar names do not work with this
   sketch.
3. Open **File → Open…** and choose `firmware\usage_lcd\usage_lcd.ino` in the project
   folder. It opens with the tabs `UsageDisplay.cpp` and `UsageDisplay.h`.
4. Plug in the Arduino, then pick the board:
   - UNO: **Tools → Board → Arduino AVR Boards → Arduino Uno**.
   - Mega: **Tools → Board → Arduino AVR Boards → Arduino Mega or Mega 2560**, and
     **Tools → Processor → ATmega2560 (Mega 2560)**.
5. Choose the port under **Tools → Port** (for example COM3). If you are not sure which
   port it is, unplug the board and see which one disappears.
6. Click **Upload** (the → button). When it finishes, the LCD shows:

   ```
   ┌────────────────┐
   │Claude usage LCD│
   │waiting for PC..│
   └────────────────┘
   ```

   - **Backlight on but no text, or only a row of blocks:** turn the small contrast screw
     (the blue trimmer on the back of the backpack) until the text appears.
   - **The board's L LED blinks fast:** the Arduino cannot find the LCD. Check the wiring.
   - **The compile fails with an error about `setWireTimeout`:** update **Arduino AVR
     Boards** (1.8.3 or newer) in **Tools → Board → Boards Manager…**.
7. If you opened the Serial Monitor, close it, because the bridge needs the COM port.

## 4. Install Python and pyserial

1. Install Python 3 in one of these two ways:
   - Use the download from <https://www.python.org/downloads/>. If the installer's first
     screen has a box **Add python.exe to PATH**, tick it.
   - Or run this in a Command Prompt: `winget install Python.Python.3.12`
2. Open a **new** Command Prompt in the project folder. To do that, open the folder in
   File Explorer, click the address bar, type `cmd` and press Enter.
3. Install pyserial:

   ```
   py -m pip install -r bridge\requirements.txt
   ```

   If `py` is not found, run `python -m pip install -r bridge\requirements.txt` instead.

## 5. Start the bridge

1. Double-click `bridge\start-bridge.cmd`. A window opens. Within a few seconds it should
   say `connected to the usage LCD on COM3` (with your port), and the LCD shows:

   ```
   ┌────────────────┐
   │Claude usage    │
   │waiting for data│
   └────────────────┘
   ```

   This means the PC and the Arduino are talking. The numbers appear after step 6.
   If the window says the port `is busy or access is denied`, close the Arduino IDE's
   Serial Monitor (or the whole IDE).
2. Close that window. Then double-click `bridge\install-autostart.cmd`. It starts the
   bridge hidden in the background, now and every time you log on to Windows. It does this
   with a shortcut named "Claude Usage LCD" in your Startup folder. Press a key to close
   its window.

Do you have other USB serial devices, such as a 3D printer or another Arduino? Then give
the bridge the port, so it never opens the others. Run this from the Command Prompt in
the project folder: `bridge\install-autostart.cmd --port COM3`. To list the ports, run
`bridge\start-bridge.cmd --list`.

The other scripts:

- `bridge\stop-bridge.cmd` stops the bridge. Autostart stays installed.
- `bridge\uninstall-autostart.cmd` removes autostart and stops the bridge.

The bridge writes its log to `%LOCALAPPDATA%\usage-lcd\bridge.log`.

## 6. Install the mod in the Claude desktop app

Use **one** of these two ways.

**A. From this folder (no command line needed)**

1. Copy the full path of the project's `mod` folder: open that folder in File Explorer,
   click the address bar and press Ctrl+C. Do not type it from memory: if your Documents
   folder is in OneDrive, the path has `OneDrive` in it.
2. Paste `%USERPROFILE%\.claude` into File Explorer's address bar, and open
   `settings.json` there with Notepad. If the file is not there, create it with Notepad:
   **File → Save as**, go to that folder, set **Save as type** to **All files (\*.\*)**,
   and save it as `settings.json` (otherwise Notepad names it `settings.json.txt`).
3. Set `CLAUDE_CODE_PLUGIN_DIRS` to that path, in an `env` block. In this file, every `\`
   in a path is written as `\\`. For a new file:

   ```json
   {
     "env": {
       "CLAUDE_CODE_PLUGIN_DIRS": "C:\\Users\\Some Name\\Documents\\claude-usage-lcd\\mod"
     }
   }
   ```

   If the file already has settings, add `"env"` next to them, separated by a comma. If
   it already has an `env` block, add the line inside that block. (If it already has a
   `CLAUDE_CODE_PLUGIN_DIRS` line, add `;` and the path to the end of its value.)

   ```json
   {
     "permissions": {
       "allow": ["Bash(git status)"]
     },
     "env": {
       "SOME_EXISTING_VARIABLE": "1",
       "CLAUDE_CODE_PLUGIN_DIRS": "C:\\Users\\Some Name\\Documents\\claude-usage-lcd\\mod"
     }
   }
   ```

4. Save the file and start a new local session (one that runs on this PC) in the
   desktop app's **Code** tab. If `/usage-lcd` is not found there, quit the desktop app
   and open it again.

**B. With the `claude` command line**

In a terminal (Command Prompt or PowerShell), start `claude` and type this at its prompt:

```
/plugin install usage-lcd --marketplace keemal94/arduino
```

Answer `y` to add the marketplace, then press Enter to install for your user. A mod
installed for your user also loads in the desktop app's Code sessions, so start a new
session there. You must type this line in a terminal, because the desktop app's Code tab
does not offer the command.

### Check that it works

1. In a new local Code session, type `/usage-lcd`. It tells you where the usage file is, for
   example `Usage file: C:\Users\Some Name\.claude\usage-lcd.json (missing or unreadable)`.
   If the command is unknown, the mod is not loaded: go back to step 6.
2. Send any message, for example `hi`, and wait for Claude's reply. Within a few seconds
   the LCD shows the bars.
3. Run `/usage-lcd` again. It now shows what was written, for example
   `5h: 76.5% left, resets in 2h 56m (…)`.
4. If the LCD still says `waiting for data`, compare two paths:
   - the path `/usage-lcd` shows;
   - the `reading …` line in the bridge log (`%LOCALAPPDATA%\usage-lcd\bridge.log`).

   If they differ, point the bridge at the right file, then try again:
   `bridge\install-autostart.cmd --file "C:\the\path\usage-lcd\shows.json"`

## Limits

- Claude Code must be signed in with a Claude subscription (such as Pro or Max). With an
  API key there is no usage data, and the LCD stays at `waiting for data`.
- The numbers arrive with Claude's replies, so they update only when Claude Code gets a
  reply. Usage elsewhere on your account (claude.ai, the desktop app's chat, your phone)
  is counted too, but the LCD shows it only after Claude Code's next reply. Between
  replies, the reset countdown keeps running. Once a window's reset time has passed, the
  LCD shows that window as 100 % left.
- Only the 5-hour and 7-day windows are shown.
- Only Code sessions that run on this PC write the usage file. Cloud sessions still use
  your plan, but the LCD sees that only after a local session's next reply.
- The bridge keeps the Arduino's COM port open. Before you upload a new sketch or open the
  Serial Monitor, run `bridge\stop-bridge.cmd`. Afterwards, start the bridge again with
  `bridge\install-autostart.cmd` (or log off and on).
- Without `--port`, the bridge opens serial ports one by one to find the board. That resets
  any Arduino-based device it opens. If you have other such devices, use `--port`.

## Troubleshooting

| What you see | Why | What to do |
| --- | --- | --- |
| Backlight on, but no text or only a row of blocks | Contrast | Turn the contrast screw on the backpack. |
| Nothing at all, or the board's L LED blinks fast | The Arduino finds no LCD on I2C | Check the four wires: SDA and SCL swapped is common; UNO A4/A5, Mega 20/21; VCC on 5V. The backlight jumper on the backpack must be on its two pins. The Arduino looks for the LCD again every 5 seconds. |
| `Claude usage LCD` / `waiting for PC..` | The bridge is not running, or it cannot open or find the board | Run `bridge\start-bridge.cmd` and read its messages. "busy or access is denied": close the Serial Monitor or the Arduino IDE. Board not found: add `--port COM3` (your port). Log: `%LOCALAPPDATA%\usage-lcd\bridge.log`. |
| `Claude usage` / `waiting for data` | The bridge runs, but there is no usage data yet | The mod is not loaded, or Claude has not replied since it was. Follow "Check that it works" above. |
| `PC link lost` / `start the bridge` | The bridge stopped sending: it was stopped, or the PC was asleep | Run `bridge\install-autostart.cmd` (or `bridge\start-bridge.cmd` to see messages). Check the log. |
| Upload fails with `Access is denied` for the COM port | The bridge holds the port | Run `bridge\stop-bridge.cmd`, upload, then run `bridge\install-autostart.cmd`. |
| Compile error about `LiquidCrystal_I2C` | The library is missing, or a different one is installed | Install "LiquidCrystal I2C" by Frank de Brabander, and remove other LiquidCrystal I2C libraries. |
| No COM port appears when the board is plugged in | Charge-only cable, or no CH340 driver | Try another cable. Install the CH340 driver (CH341SER) from WCH, the chip's maker. |
| `another bridge is already running` | Autostart already runs one | Nothing to do. To run it in a window instead, run `bridge\stop-bridge.cmd` first. |

## macOS

On a Mac, the Arduino, the LCD, the wiring and the firmware are the same as on Windows.
Only the bridge's scripts and a few file locations differ. Everything below is done in
Finder and in apps; you do not have to type commands. You need a Mac with the Claude
desktop app, signed in with a Claude subscription (such as **Pro or Max**).

### M1. Get the files

1. On GitHub, click **Code → Download ZIP**. Safari unpacks it in your **Downloads**
   folder; with another browser, double-click the ZIP in Finder. The folder's name starts
   with `arduino-`. Rename it to `claude-usage-lcd`.
2. Move the folder where you keep things, for example your **Documents** folder. The
   autostart (M5) runs its own copy of the bridge, so the folder may be anywhere. If you
   move it later, repeat M5 and M6.

### M2. Wire the LCD

As in [step 2](#2-wire-the-lcd).

### M3. Upload the firmware

Follow [step 3](#3-upload-the-firmware) with Arduino IDE 2 for macOS: download it from
<https://www.arduino.cc/en/software>, open the `.dmg` and drag **Arduino IDE** to
**Applications**. What differs on a Mac:

- **Tools → Port** shows the board as `/dev/cu.usbserial-…` (most clones, with a CH340
  chip) or `/dev/cu.usbmodem…` (an original UNO or Mega), not as COM3. Leave
  `/dev/cu.Bluetooth-Incoming-Port` and the other Bluetooth entries alone.
- macOS has a driver for the CH340 chip built in, so you usually do not install one. Only
  if neither `/dev/cu.usbserial-…` nor `/dev/cu.usbmodem…` appears, with a cable you know
  carries data, install the CH34x driver from WCH, the chip's maker. The port is then
  called `/dev/cu.wchusbserial…`.
- If you opened the Serial Monitor, close it, because the bridge needs the port.

### M4. Install Python

Download the **macOS installer** of Python 3 from
<https://www.python.org/downloads/macos/>, open it and click through it. A Python from
Homebrew works too. You do not run any `pip` command: the bridge's scripts make a Python
environment of their own and put pyserial in it. The first time, that needs the internet.

### M5. Start the bridge

1. In Finder, open the project folder, then `bridge`, then `macos`, and double-click
   `start-bridge.command`. A Terminal window opens.
   - If macOS says it cannot open the file or that Apple could not verify it, click
     **Done**. Open **System Settings → Privacy & Security**, scroll down to the message
     about `start-bridge.command` and click **Open Anyway**. Double-click the file again
     and click **Open** (macOS may ask for your password). You do this once: after that,
     the other scripts in this folder open without asking. (On macOS 14 and older you can
     instead Control-click the file, choose **Open** and click **Open**.)
   - If the project is in Documents, Desktop or Downloads, macOS may ask whether
     **Terminal** may access that folder. Click **Allow**.
2. The first time, the window says it is setting up the Python environment; that takes
   about a minute. Then, within a few seconds, it should say
   `connected to the usage LCD on /dev/cu.usbserial-110` (with your port), and the LCD
   shows:

   ```
   ┌────────────────┐
   │Claude usage    │
   │waiting for data│
   └────────────────┘
   ```

   The numbers appear after M6. If the window says the port `is busy or access is
   denied`, quit the Arduino IDE.
3. Close the Terminal window (click **Terminate** if asked). Then double-click
   `install-autostart.command`. It copies the bridge to
   `~/Library/Application Support/usage-lcd` and runs it in the background, now and every
   time you log in. Press Return to close its window.

   macOS then shows a notification **Background Items Added** that names "python" or
   "Python". That is the bridge. It must stay switched on in **System Settings → General →
   Login Items & Extensions**, under **Allow in the Background**.

The other scripts in `bridge/macos`:

- `stop-bridge.command` stops the bridge, for example before you upload a sketch.
  Autostart stays installed: the bridge starts again when you next log in, or when you
  double-click `install-autostart.command`.
- `uninstall-autostart.command` removes autostart and stops the bridge.

The bridge writes its log to `~/Library/Logs/usage-lcd/bridge.log`. To open it, choose
**Go → Go to Folder…** in Finder, type `~/Library/Logs/usage-lcd`, press Return and
double-click `bridge.log`. Next to it, `launchd.log` only has something in it when the
autostart bridge could not start at all.

The bridge only opens USB serial ports, and tries `/dev/cu.usbmodem…` and
`/dev/cu.usbserial…` first. It never opens Bluetooth ports. Do you have other USB serial
devices, such as a 3D printer or another Arduino? Then give the bridge the port: open
**Terminal** (in Applications → Utilities), drag `install-autostart.command` from Finder
into its window, type a space and `--port /dev/cu.usbserial-110` (your port), and press
Return. To see the ports, drag `start-bridge.command` into Terminal instead and add
` --list`.

### M6. Install the mod in the Claude desktop app

1. Copy the full path of the project's `mod` folder: in Finder, Control-click the `mod`
   folder, hold down **Option**, and choose **Copy "mod" as Pathname**.
2. In Finder, choose **Go → Go to Folder…**, type `~/.claude` and press Return. (Finder
   hides that folder otherwise.) Control-click `settings.json` and choose **Open With →
   TextEdit**. If there is no `settings.json`: open TextEdit, choose **Format → Make
   Plain Text**, and save the new file in that folder as `settings.json`. When TextEdit
   asks, click **Use .json**.
3. In TextEdit, first switch off **Edit → Substitutions → Smart Quotes**. Otherwise
   TextEdit turns `"` into curly quotes, and Claude cannot read the file.
4. Set `CLAUDE_CODE_PLUGIN_DIRS` to the path you copied, in an `env` block. On a Mac a
   path uses `/`, and you write it as it is. For a new file:

   ```json
   {
     "env": {
       "CLAUDE_CODE_PLUGIN_DIRS": "/Users/yourname/Documents/claude-usage-lcd/mod"
     }
   }
   ```

   If the file already has settings, add `"env"` next to them, separated by a comma. If
   it already has an `env` block, add the line inside that block. (If it already has a
   `CLAUDE_CODE_PLUGIN_DIRS` line, add `:` and the path to the end of its value. On a Mac
   folders are separated by `:`, not `;`.) There is an example in
   [step 6](#6-install-the-mod-in-the-claude-desktop-app).
5. Save the file and start a new local session in the desktop app's **Code** tab. If
   `/usage-lcd` is not found there, quit the desktop app (**Claude → Quit Claude**) and
   open it again.

You can also install the mod with the `claude` command line, as in step 6 B, in Terminal.

### M7. Check that it works

As in [Check that it works](#check-that-it-works), with Mac paths:

1. In a new local Code session, type `/usage-lcd`. It answers with
   `Usage file: /Users/yourname/.claude/usage-lcd.json (missing or unreadable)`.
2. Send any message, for example `hi`, and wait for Claude's reply. Within a few seconds
   the LCD shows the bars, and `/usage-lcd` shows what was written.
3. If the LCD still says `waiting for data`, compare the path `/usage-lcd` shows with the
   `reading …` line in `~/Library/Logs/usage-lcd/bridge.log`. If they differ, give the
   bridge that file: drag `install-autostart.command` into Terminal, type a space and
   `--file "/the/path/usage-lcd/shows.json"`, and press Return.

### macOS troubleshooting

The table in [Troubleshooting](#troubleshooting) holds for the LCD itself. On a Mac,
also:

| What you see | Why | What to do |
| --- | --- | --- |
| `Claude usage LCD` / `waiting for PC..` | The bridge is not running, or it cannot open or find the board | Double-click `start-bridge.command` and read its messages. "busy or access is denied": quit the Arduino IDE. Log: `~/Library/Logs/usage-lcd/bridge.log`. |
| The Arduino IDE's upload fails with `Resource busy`, or cannot open the port | The bridge holds the port, so the two never use the board at once | Double-click `stop-bridge.command`, upload, then double-click `install-autostart.command`. |
| No `/dev/cu.usbserial-…` or `/dev/cu.usbmodem…` under **Tools → Port** | Charge-only cable, a USB hub or adapter, or (rarely) the driver | Try another cable, and plug it into the Mac directly. Then install WCH's CH34x driver. |
| "cannot be opened", "Apple could not verify" or "unidentified developer" | Gatekeeper checks each downloaded script | **System Settings → Privacy & Security → Open Anyway**, as in M5. |
| Double-clicking a `.command` file opens it in a text editor, or says you do not have permission | The tool that unpacked the ZIP dropped the files' "executable" flag | Download the ZIP again and let Safari or Finder unpack it. |
| `Python 3 was not found` | Python is not installed | Install it as in M4, then double-click the script again. |
| `Could not install pyserial` | The first setup needs the internet | Connect to the internet and double-click the script again. |
| `launchd did not start the bridge`, or the bridge does not start at login | Its background item is switched off | In **System Settings → General → Login Items & Extensions → Allow in the Background**, switch on "python" (or "Python"). Then double-click `install-autostart.command`. |
| `The bridge stopped right away` | Usually a wrong `--port` or `--file`, or a Python that Homebrew removed | Double-click `start-bridge.command` to see the messages. Double-click `install-autostart.command` again: it also makes the Python environment again. |
| `PC link lost` / `start the bridge` after the Mac slept | Nothing reached the LCD while the Mac slept | After waking, the bridge finds the board again within seconds. If not, double-click `install-autostart.command`. |
| `another bridge is already running` | Autostart already runs one | Nothing to do. To watch it in a window instead, double-click `stop-bridge.command` first. |

## For developers

The contract between the three parts is [docs/PROTOCOL.md](docs/PROTOCOL.md). The test
suites run on Linux or macOS. Run them from the repository folder:

| Part | Commands |
| --- | --- |
| Mod | `claude plugin validate mod`, `claude plugin validate .` (the marketplace file), `claude plugin test mod` |
| Bridge | `python3 -m pip install pyserial pytest`, then `python3 -m pytest bridge/tests` |
| Firmware logic | `sh firmware/host_test/build.sh` (g++; it builds and runs the host tests, and builds `lcdsim`, into `firmware/host_test/build/`) |
| Sketch | `arduino-cli compile -b arduino:avr:uno firmware/usage_lcd` and `arduino-cli compile -b arduino:avr:mega:cpu=atmega2560 firmware/usage_lcd` (needs the LiquidCrystal I2C library) |
| End to end | `python3 -m pytest tests/e2e` (needs g++. The live serial test needs a POSIX pty, and the fixture check needs node 22.18 or newer.) |

`tests/fixtures/*.json` are byte for byte what the mod writes. The mod's tests hold a copy
of `normal.json`, and the bridge's tests read it. `tests/e2e` checks both copies, and runs
every fixture through the bridge and the display code. To see a frame yourself, run
`printf 'U|765|9008|410|311408\n#show\n' | firmware/host_test/build/lcdsim`.

On macOS, Homebrew's Python refuses `pip install` outside a virtual environment (PEP 668).
Make one in the repository folder (`.venv` is gitignored) and run the Python suites with it:
`python3 -m venv .venv`, `.venv/bin/python -m pip install pyserial pytest shellcheck-py`,
then `.venv/bin/python -m pytest bridge/tests tests`. With shellcheck there, the bridge's
tests also check `bridge/macos`. On a Mac they run those scripts against a throwaway home
folder, never against the real launchd agent.
