# MagTag Commute Dashboard — Claude Context

## What this project does

Displays real-time commute time (Google Routes API) on a MagTag e-ink display.
NeoPixels interpolate Green→Red based on configurable good/bad thresholds.
The bottom half of the display is reserved for a future second data source (train/transit times or similar — all text fields, no new graphics needed).

---

## Hardware

**Confirmed from `boot_out.txt`:**
```
Adafruit CircuitPython 10.1.3 on 2026-02-20; Adafruit MagTag with ESP32S2
Board ID: adafruit_magtag_2.9_grayscale
```

- **MCU**: ESP32S2
- **Display**: 2.9" grayscale e-ink, 296×128 px — driver is **ILI0373 or SSD1680** (auto-detected at runtime, see below)
- **Display object type in CP 10**: `EPaperDisplay` (accessed as `board.DISPLAY`)
- **Connected via**: USB serial on COM4
- **CircuitPython version**: 10.1.3

### Hardware generation — board ID does NOT identify it

`adafruit_magtag_2.9_grayscale` is the board ID for **both** the original MagTag (ILI0373 display, ~15s full refresh) and the 2025 edition (SSD1680 display, ~1s refresh). They share the same board ID.

CircuitPython 10's `board.c` auto-detects the display chip at runtime by reading SSD1680 User ID register `0x2e`:
- Returns `0xFF` → ILI0373 (original)
- Returns `0x00` or `0x44` → SSD1680 (2025 edition)

Source: https://github.com/adafruit/circuitpython/blob/main/ports/espressif/boards/adafruit_magtag_2.9_grayscale/board.c

**The hardware generation is currently unconfirmed for this unit.** To determine which display is present, observe how long a successful refresh takes once the display is working: ~1s = SSD1680 (2025), ~15s = ILI0373 (original). This matters for calibrating the boot settling delay in `safe_refresh()`.

---

## Display layout

```
+---------------------------296px--------------------------+
|  [CAR  ] [ commute time text — slot 1          ]        |  y=0..63
|  [ICON ] [                                     ]        |
+-----------------------------------------------------------+
|  [TRAIN] [ train time text — slot 2            ]        |  y=64..127
|  [ICON ] [                              [slot3]]        |
+-----------------------------------------------------------+
                                           ^timestamp (slot 3, bottom-right)
```

- **Slot 0**: Hidden placeholder (position overlaps car icon area; always set to `""`)
- **Slot 1**: Commute time — `"43 min"` — `text_scale=3`, position `(90, 32)`
- **Slot 2**: Train/transit time — currently `"--:--"` — `text_scale=2`, position `(90, 96)`
- **Slot 3**: Fetch timestamp — `"as of 01:42"` — `text_scale=1`, position `(228, 120)`
- **Car icon**: 16×10 pixel art at `ICON_SCALE=4` → 64×40 px, `Group(scale=4, x=10, y=12)`
- **Train icon**: 16×9 pixel art at `ICON_SCALE=4` → 64×36 px, `Group(scale=4, x=10, y=78)`

Icons are drawn with `make_icon()` → `displayio.Bitmap` + `displayio.Palette` + `displayio.TileGrid`, appended to `magtag.graphics.root_group`.

---

## APIs

### Google Routes API v2
- Endpoint: `https://routes.googleapis.com/directions/v2:computeRoutes`
- Auth: `?key=GOOGLE_ROUTES_API_KEY` (URL param — not header, because `adafruit_requests` may drop custom headers)
- Field mask: `?fields=routes.duration`
- Method: POST, JSON body with `origin`, `destination`, `travelMode: DRIVE`, `routingPreference: TRAFFIC_AWARE`
- Response: `routes[0].duration` as a string like `"1800s"`

### Adafruit IO Time API
- Endpoint: `https://io.adafruit.com/api/v2/{username}/integrations/time/strftime`
- Auth: `?x-aio-key=ADAFRUIT_AIO_KEY`
- Format param: `&fmt=%25I%3A%25M` → `%I:%M` (12-hr hour:minute)
- Used for display timestamp only (MagTag has no RTC)

---

## Library source code locations

**Human-readable Python source** (for debugging — read these, not the .mpy files on device):
```
C:\Users\darth\AppData\Roaming\Code\User\globalStorage\wmerkens.vscode-circuitpython-v2\bundle\20260307\adafruit-circuitpython-bundle-py-20260307\lib\
  adafruit_magtag\
    magtag.py       — MagTag class, set_text() override, refresh() method
    graphics.py     — Graphics class wrapping board.DISPLAY
    network.py      — Network helper
    peripherals.py  — NeoPixels, buttons, speaker
  adafruit_portalbase\
    __init__.py     — PortalBase base class: add_text(), set_text(), root_group
    graphics.py     — GraphicsBase: display.root_group assignment, set_background()
    network.py      — NetworkBase
    wifi_esp32s2.py — WiFi implementation
```

**Compiled .mpy versions on device** are in `lib\` in this project directory. They can't be read but match the above source.

---

## Critical display findings (from active debugging, CP 10.1.3)

### `EPaperDisplay` API in CircuitPython 10

Confirmed by running diagnostics on the actual hardware:

| Property | Exists | Notes |
|----------|--------|-------|
| `auto_refresh` | **NO** | Does not exist on EPaperDisplay in CP 10 — will raise `AttributeError` |
| `time_to_refresh` | YES | Returns float seconds until refresh is allowed |
| `busy` | YES | Returns bool — True while display is updating |
| `refresh()` | YES | Raises `RuntimeError: Refresh too soon` if called too early |

**Do not reference `display.auto_refresh` anywhere** — it doesn't exist.

### The two `auto_refresh` concepts (critical distinction)

The MagTag library has two completely independent `auto_refresh` things:

1. **`Graphics.auto_refresh`** (Python attribute on the Graphics object)  
   - Set to `False` by `MagTag.__init__()` ([magtag.py:100](C:\Users\darth\AppData\Roaming\Code\User\globalStorage\wmerkens.vscode-circuitpython-v2\bundle\20260307\adafruit-circuitpython-bundle-py-20260307\lib\adafruit_magtag\magtag.py))
   - Controls whether `set_background()` / `qrcode()` call `display.refresh()` after changes
   - **Does not affect the hardware display**

2. **`display.auto_refresh`** (hardware property)  
   - Does **not exist** on `EPaperDisplay` in CP 10 — raises `AttributeError`

### `MagTag.refresh()` has a silent infinite loop

The library's own `refresh()` method ([magtag.py:197-206](C:\Users\darth\AppData\Roaming\Code\User\globalStorage\wmerkens.vscode-circuitpython-v2\bundle\20260307\adafruit-circuitpython-bundle-py-20260307\lib\adafruit_magtag\magtag.py)) is:
```python
def refresh(self) -> None:
    while True:
        try:
            self.graphics.display.refresh()
            return
        except RuntimeError:
            time.sleep(1)
```
**This loops forever with zero output if `display.refresh()` always raises RuntimeError.**  
This was the original cause of the display hang — the code silently retried forever.  
**Do not call `magtag.refresh()` directly.** Use `safe_refresh()` defined in `code.py` instead.

### "Refresh too soon" on first boot

The display raises `RuntimeError: Refresh too soon` immediately after boot before any refresh has been attempted. `time_to_refresh` returns `0.0` at that point (no previous refresh to count from), so simply waiting on it won't help. The hardware needs a settling period after power-up that the software property doesn't track. The exact duration for ILI0373 is still being characterized — this is the active debugging frontier as of the last session.

### Display refresh pattern — always use this

```python
# Set all text fields with auto_refresh=False to batch changes
magtag.set_text(val1, 0, auto_refresh=False)
magtag.set_text(val2, 1, auto_refresh=False)
magtag.set_text(val3, 2, auto_refresh=False)
magtag.set_text(val4, 3, auto_refresh=False)
# Trigger ONE refresh at the end
safe_refresh()
```

Never pass `auto_refresh=True` (the default) to `set_text()` — it calls the library's infinite-loop `magtag.refresh()`.

### Extensibility rule for new text fields

To add a new text field for a future API:
1. `magtag.add_text(text_position=(...), text_scale=N, text_color=0x000000)` — returns the new slot index
2. In `update_display()` (or a new parallel update function), call `magtag.set_text(val, index, auto_refresh=False)`
3. The single `safe_refresh()` at the end handles everything — no additional refresh calls needed

---

## WiFi setup — why it's custom

`magtag.network.connect()` always reconnects to `CIRCUITPY_WIFI_SSID` only. To support multiple fallback SSIDs (home, work, phone hotspot), `code.py` manages the radio directly:
```python
wifi.radio.connect(ssid, password)
pool = socketpool.SocketPool(wifi.radio)
magtag.network.requests = adafruit_requests.Session(pool, ssl_context)
```
This bypasses the library's connection helper but reuses its `requests` session slot so all API calls still go through `magtag.network.requests`.

---

## Settings

Stored in `/settings.toml` on the device (not in this repo).

| Key | Required | Description |
|-----|----------|-------------|
| `CIRCUITPY_WIFI_SSID` | Yes | Primary WiFi |
| `CIRCUITPY_WIFI_PASSWORD` | Yes | Primary WiFi password |
| `WIFI_SSID_2` / `WIFI_PASSWORD_2` | No | Fallback SSID |
| `WIFI_SSID_3` / `WIFI_PASSWORD_3` | No | Second fallback |
| `GOOGLE_ROUTES_API_KEY` | Yes | Google Routes API v2 key |
| `ORIGIN_ADDRESS` | Yes | Commute start (address or Plus Code) |
| `DEST_ADDRESS` | Yes | Commute end (address or Plus Code) |
| `ADAFRUIT_AIO_USERNAME` | Yes | Adafruit IO username |
| `ADAFRUIT_AIO_KEY` | Yes | Adafruit IO key |
| `TIMEZONE` | No | e.g. `"America/Los_Angeles"` (default) |
| `GOOD_COMMUTE_MINS` | No | Green threshold in minutes (default 45) |
| `BAD_COMMUTE_MINS` | No | Red threshold in minutes (default 70) |
| `UPDATE_INTERVAL` | No | Seconds between polls (default 600) |

---

## Key documentation links

- Adafruit MagTag guide: https://learn.adafruit.com/adafruit-magtag
- Creating MagTag projects with CircuitPython: https://learn.adafruit.com/creating-magtag-projects-with-circuitpython
- CircuitPython displayio guide: https://learn.adafruit.com/circuitpython-display-support-using-displayio
- EPaperDisplay API reference: https://docs.circuitpython.org/en/latest/shared-bindings/epaperdisplay/
- adafruit_magtag library API: https://docs.circuitpython.org/projects/magtag/en/stable/api.html
- adafruit_portalbase library: https://docs.circuitpython.org/projects/portalbase/en/stable/

---

## Display stuck-state recovery

If `display.busy` is stuck `True` indefinitely (confirmed by `safe_refresh()` timing out at 60s), the display is in a stuck state from a previous interrupted refresh. **A soft reset / code reload will NOT clear this.** You must:
1. Unplug USB
2. Wait ~30 seconds
3. Plug USB back in

Once unstuck, the display works normally and `busy=False` at boot. Verified 2026-04-18: same `code.py` that hung for 60+ seconds worked fine after power cycle.

## Active debugging status

**Symptom:** Display never updates. `display.busy` is stuck `True` indefinitely from boot. `display.refresh()` raises `RuntimeError: Refresh too soon` — but the cause is `busy=True`, not a time interval (`time_to_refresh` is `0.0` throughout).

**Confirmed by serial output:**
- At boot: `time_to_refresh=0.0`, `busy=True`
- After 60s+ of waiting with `safe_refresh()` polling every 0.5s: still `busy=True`
- Every refresh attempt fails with "Refresh too soon"

**Ruled out:**
- Not a library `auto_refresh` issue — `EPaperDisplay` in CP 10 has no `auto_refresh` property
- Not a `time_to_refresh` interval issue — that value is `0.0` throughout
- Not insufficient retries — 60s of busy-polling didn't clear it
- Not the silent infinite-loop hang in `magtag.refresh()` (the original bug) — we bypass that with `safe_refresh()`

**Current hypothesis:** Hardware or driver-level issue. The display's BUSY pin is either stuck, the display is mid-initialization and never completes, or the display/driver pairing mismatch (see auto-detection note under Hardware) is producing a bad state.

**Next diagnostic step:** Flash [adafruit-circuitpython-bundle-10.x-mpy-20260310/examples/magtag/magtag_simpletest.py](adafruit-circuitpython-bundle-10.x-mpy-20260310/examples/magtag/magtag_simpletest.py) as `code.py` (back up current first). This is Adafruit's reference 34-line example — calls `magtag.set_text("Hello World")` with default `auto_refresh=True`. If it hangs with no output, it's the library's silent retry loop confirming `busy` is genuinely stuck → hardware-level issue. If it works and displays "Hello World", the problem is something in our code/setup, not the board.

**If hardware issue confirmed:** Things to try —
- Full power cycle (unplug USB, hold reset, re-plug)
- Press the reset button after boot completes (sometimes shakes the display out of a bad init)
- Re-flash the CircuitPython UF2 bootloader
- Check whether `board.c` display auto-detection is making the right call — the display register read could be ambiguous on some units

**Current code state:** `safe_refresh()` at [code.py:194-214](code.py#L194-L214) waits for `busy=False` with a 60s timeout before calling `refresh()`. Boot diagnostic block at [code.py:139-144](code.py#L139-L144) prints display properties. Remove both blocks once display works.
