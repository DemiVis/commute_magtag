"""
MagTag Commute Dashboard
------------------------
Fetches Google Routes API commute times and displays them on the top half
of the E-Ink display. Changes MagTag NeoPixels from Green (Good) to Red (Bad).
Leaves bottom half available for future expansions (e.g., train schedules).

Uses regular sleep (not deep sleep) between updates so NeoPixels stay on.

Dependencies (in /lib):
Folders:
- adafruit_magtag
- adafruit_portalbase
- adafruit_bitmap_font
- adafruit_display_text
- adafruit_imageload
- adafruit_io
- adafruit_minimqtt

Files:
- adafruit_connection_manager.mpy
- adafruit_fakerequests.mpy
- adafruit_miniqr.mpy
- adafruit_requests.mpy
- adafruit_ticks.mpy
- neopixel.mpy
- simpleio.mpy

Settings (in /settings.toml):
- CIRCUITPY_WIFI_SSID       Primary WiFi network name
- CIRCUITPY_WIFI_PASSWORD   Primary WiFi password
- WIFI_SSID_2               (optional) Fallback WiFi SSID
- WIFI_PASSWORD_2           (optional) Fallback WiFi password
- WIFI_SSID_3               (optional) Second fallback WiFi SSID
- WIFI_PASSWORD_3           (optional) Second fallback WiFi password
- GOOGLE_ROUTES_API_KEY     Google Routes API v2 key
- ORIGIN_ADDRESS            Commute start address or Plus Code
- DEST_ADDRESS              Commute end address or Plus Code
- GOOD_COMMUTE_MINS         Minutes at or below which LEDs are full green (default: 45)
- BAD_COMMUTE_MINS          Minutes at or above which LEDs are full red (default: 70)
- UPDATE_INTERVAL           Seconds between API polls (default: 600)
- ADAFRUIT_AIO_USERNAME     Adafruit IO username (for fetch time)
- ADAFRUIT_AIO_KEY          Adafruit IO API key (for fetch time)
- TIMEZONE                  Timezone string e.g. "America/Los_Angeles" (for fetch time)

Constants (edit in code.py):
- BRIGHTNESS_PER            NeoPixel brightness, 0-100 percent (default: 10)
- ICON_SCALE                Pixel scale factor for bitmap icons (default: 4)
"""

import os
import time
import traceback
import board
import displayio
import supervisor
import wifi
import socketpool
import ssl
import adafruit_requests
from adafruit_magtag.magtag import MagTag

# Disable auto-reload so saving code.py during development doesn't interrupt
# an in-progress e-ink refresh (which leaves the display chip stuck busy until
# a full power cycle). Press Ctrl-D in the REPL or the reset button to reload.
supervisor.runtime.autoreload = False

# If a previous run was interrupted mid-refresh, the display's BUSY line may
# still be asserted. Wait for it to clear before any MagTag() init, which can
# issue SPI commands the display will reject while busy.
_boot_start = time.monotonic()
while board.DISPLAY.busy:
    if time.monotonic() - _boot_start > 30:
        print("WARN: display busy=True after 30s at boot — may need power cycle")
        break
    time.sleep(0.5)
_boot_waited = time.monotonic() - _boot_start
if _boot_waited > 0.5:
    print(f"Waited {_boot_waited:.1f}s for display busy to clear at boot")

# ==========================================
# SETTINGS VALIDATION
# ==========================================

REQUIRED_SETTINGS = [
    "CIRCUITPY_WIFI_SSID",
    "CIRCUITPY_WIFI_PASSWORD",
    "GOOGLE_ROUTES_API_KEY",
    "ORIGIN_ADDRESS",
    "DEST_ADDRESS",
    "ADAFRUIT_AIO_USERNAME",
    "ADAFRUIT_AIO_KEY",
]

missing = [k for k in REQUIRED_SETTINGS if not os.getenv(k)]
if missing:
    print("ERROR: Missing required settings in settings.toml:")
    for k in missing:
        print(f"  - {k}")
    raise RuntimeError("Halting — fix settings.toml and reload.")

print("All required settings present.")

# ==========================================
# CONSTANTS
# ==========================================

BRIGHTNESS_PER = 5  # NeoPixel brightness percentage (0-100)
ICON_SCALE = 4       # Each icon pixel renders as 4×4 → icons are 64×40 and 64×36 px

# Pixel art icons: 16 px wide, 'X' = black, ' ' = white.
# At ICON_SCALE=4 the car is 64×40 px.
CAR_PIXELS = [
    "        XXXXXX  ",
    "       X  X   X ",
    "      X   X   X ",
    "     X    X   X ",
    " XXXXX    X   X ",
    "XXXXXXXXXXXXXXXX",
    "XXXXXXXXXXXXXXXX",
    " XXXXXXXXXXXXXX ",
    "   XX      XX   ",
    "   XX      XX   ",
]

# At ICON_SCALE=4 the train is 64×36 px.
TRAIN_PIXELS = [
    "XXXXXXXXXXXXXXXX",
    "X              X",
    "X              X",
    "XXXXXXXXXXXXXXXX",
    "X     X  X     X",
    "X     X  X     X",
    "XXXXXXX  XXXXXXX",
    "XXXXXXXXXXXXXXXX",
    " X X        X X ",
]


def make_icon(pixel_rows):
    """Build a displayio.TileGrid from a list of pixel-row strings."""
    h = len(pixel_rows)
    w = len(pixel_rows[0])
    bmp = displayio.Bitmap(w, h, 2)
    pal = displayio.Palette(2)
    pal[0] = 0xFFFFFF  # white (background)
    pal[1] = 0x000000  # black (foreground)
    for row_y, row in enumerate(pixel_rows):
        for col_x, ch in enumerate(row):
            bmp[col_x, row_y] = 1 if ch == "X" else 0
    return displayio.TileGrid(bmp, pixel_shader=pal)

# ==========================================
# HARDWARE & DISPLAY SETUP
# ==========================================

magtag = MagTag()

# --- Display diagnostics ---
_d = magtag.graphics.display
print(f"display type = {type(_d)}")
print(f"  width={_d.width} height={_d.height} rotation={_d.rotation}")
print(f"  time_to_refresh = {_d.time_to_refresh}")
print(f"  busy = {_d.busy}")

# Text slot 0: hidden placeholder — car icon is a bitmap, not text
magtag.add_text(
    text_position=(10, 32),
    text_scale=2,
    text_color=0x000000,
)

# Text slot 1: Commute time (top half, starts after 64 px icon + gap)
magtag.add_text(
    text_position=(90, 32),
    text_scale=3,
    text_color=0x000000,
)

# Text slot 2: Train time (bottom half, starts after 64 px icon + gap)
magtag.add_text(
    text_position=(90, 96),
    text_scale=2,
    text_color=0x000000,
)

# Text slot 3: Fetch timestamp — small, bottom-right corner
# Scale-1 font is 6×12 px; "as of HH:MM"
magtag.add_text(
    text_position=(228, 120),
    text_scale=1,
    text_color=0x000000,
)

# Car icon: 64×40 px, vertically centered on top half (center y=32 → top y=12)
car_group = displayio.Group(scale=ICON_SCALE, x=10, y=12)
car_group.append(make_icon(CAR_PIXELS))
magtag.graphics.root_group.append(car_group)

# Train icon: 64×36 px, vertically centered on bottom half (center y=96 → top y=78)
train_group = displayio.Group(scale=ICON_SCALE, x=10, y=78)
train_group.append(make_icon(TRAIN_PIXELS))
magtag.graphics.root_group.append(train_group)

# ==========================================
# NETWORK SETUP
# ==========================================

def safe_refresh(max_wait=60):
    """Wait for display to be idle, then refresh. Bounded by max_wait seconds."""
    display = magtag.graphics.display
    start = time.monotonic()
    while display.busy:
        if time.monotonic() - start > max_wait:
            print(f"  busy=True after {max_wait}s — giving up")
            return False
        time.sleep(0.5)
    waited = time.monotonic() - start
    if waited > 0.5:
        print(f"  busy cleared after {waited:.1f}s")
    while display.time_to_refresh > 0:
        time.sleep(0.5)
    try:
        display.refresh()
        while display.busy:
            pass
        return True
    except RuntimeError as e:
        print(f"  refresh() failed: {e}")
        return False


def show_error(line1, line2="", fetch_time=None):
    """Blue LEDs + error message on display. Used for WiFi and API failures."""
    magtag.peripherals.neopixels.fill((0, 0, 255))
    magtag.peripherals.neopixels.brightness = BRIGHTNESS_PER / 100.0
    magtag.peripherals.neopixel_disable = False
    time_str = f"upd: {fetch_time}" if fetch_time else "upd: --:--"
    magtag.set_text("", 0, auto_refresh=False)
    magtag.set_text(line1, 1, auto_refresh=False)
    magtag.set_text(line2, 2, auto_refresh=False)
    magtag.set_text(time_str, 3, auto_refresh=False)
    if safe_refresh():
        print("Error display refreshed.")
    else:
        print("Error display refresh failed.")


# Build ordered list of (ssid, password) candidates from settings.toml.
# Primary is always first; backups _2 and _3 are included only if defined.
def _wifi_candidates():
    candidates = [(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD", ""))]
    for n in (2, 3):
        ssid = os.getenv(f"WIFI_SSID_{n}")
        if ssid:
            candidates.append((ssid, os.getenv(f"WIFI_PASSWORD_{n}", "")))
    return candidates

def _try_connect_wifi():
    """Try each candidate SSID in order. Returns True on first success."""
    candidates = _wifi_candidates()
    for ssid, password in candidates:
        print(f"Trying WiFi: {ssid}")
        try:
            wifi.radio.connect(ssid, password)
            time.sleep(2)  # let DHCP settle (DNS can lag after connect)
            print(f"Connected to {wifi.radio.ap_info.ssid!r}")
            print(f"  ip={wifi.radio.ipv4_address} dns={wifi.radio.ipv4_dns}")
            return True
        except Exception as e:
            print(f"  Failed ({ssid}): {e}")
    print(f"All {len(candidates)} SSID(s) failed.")
    return False

# Connect to WiFi — retry with blue-LED error state until successful.
# We handle the radio connection ourselves so backup SSIDs work correctly.
# magtag.network.connect() always reconnects to CIRCUITPY_WIFI_SSID so we
# skip it and build the requests session directly instead.
while True:
    try:
        if _try_connect_wifi():
            pool = socketpool.SocketPool(wifi.radio)
            ssl_context = ssl.create_default_context()
            magtag.network.requests = adafruit_requests.Session(pool, ssl_context)
            print("Requests session ready.")
            break
    except Exception as e:
        print(f"WiFi setup error: {e}")
        traceback.print_exception(e)
    show_error("No WiFi", "retrying...")
    time.sleep(10)

# ==========================================
# HELPER FUNCTIONS
# ==========================================


def get_commute_time():
    """Fetches commute duration from Google Routes API v2. Returns minutes or None."""
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY")
    # Key and field mask passed as URL params — adafruit_requests may drop custom headers
    url = (
        f"https://routes.googleapis.com/directions/v2:computeRoutes"
        f"?key={api_key}&fields=routes.duration"
    )

    headers = {
        "Content-Type": "application/json",
    }

    payload = {
        "origin": {"address": os.getenv("ORIGIN_ADDRESS")},
        "destination": {"address": os.getenv("DEST_ADDRESS")},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }

    # print("Equivalent curl command:")
    # print(
    #     f"curl -X POST \"{url}\" \\\n"
    #     f"  -H \"Content-Type: application/json\" \\\n"
    #     f"  -d '{{\"origin\":{{\"address\":\"{os.getenv('ORIGIN_ADDRESS')}\"}}"
    #     f",\"destination\":{{\"address\":\"{os.getenv('DEST_ADDRESS')}\"}}"
    #     f",\"travelMode\":\"DRIVE\",\"routingPreference\":\"TRAFFIC_AWARE\"}}'"
    # )
    try:
        response = magtag.network.requests.post(url, json=payload, headers=headers)
        data = response.json()
        response.close()

        # Google returns duration as a string like "1800s"
        duration_str = data.get("routes", [{}])[0].get("duration", "0s")
        duration_seconds = int(duration_str.replace("s", ""))
        if duration_seconds == 0:
            print("Fetching route data... 0s (bad response):")
            print(data)
        else:
            print(f"Fetching route data... {duration_seconds / 60.0:.1f} min")
        return duration_seconds / 60.0

    except Exception as e:
        print(f"Fetching route data... error: {e}")
        traceback.print_exception(e)
        return None


def get_fetch_time():
    """Returns the current local time as 'HH:MM' from Adafruit IO, or None on failure."""
    username = os.getenv("ADAFRUIT_AIO_USERNAME")
    key = os.getenv("ADAFRUIT_AIO_KEY")
    tz = os.getenv("TIMEZONE", "America/Los_Angeles")
    # %25I = %I (12-hr hour), %3A = ':', %25M = %M (minute)
    url = (
        f"https://io.adafruit.com/api/v2/{username}/integrations/time/strftime"
        f"?x-aio-key={key}&tz={tz}&fmt=%25I%3A%25M"
    )
    try:
        response = magtag.network.requests.get(url)
        result = response.text.strip()
        response.close()
        print(f"Fetching time... {result}")
        return result
    except Exception as e:
        print(f"Fetching time... error: {e}")
        traceback.print_exception(e)
        return None


def update_leds(commute_mins):
    """Linearly interpolates LED colors from Green (good) to Red (bad) at BRIGHTNESS_PER brightness."""
    good = float(os.getenv("GOOD_COMMUTE_MINS", "45"))
    bad = float(os.getenv("BAD_COMMUTE_MINS", "70"))

    if commute_mins <= good:
        ratio = 0.0
    elif commute_mins >= bad:
        ratio = 1.0
    else:
        ratio = (commute_mins - good) / (bad - good)

    red = int(ratio * 255)
    green = int((1.0 - ratio) * 255)
    magtag.peripherals.neopixels.fill((red, green, 0))
    magtag.peripherals.neopixels.brightness = BRIGHTNESS_PER / 100.0
    magtag.peripherals.neopixel_disable = False


def update_display(commute_mins, fetch_time=None):
    """Updates the E-Ink display with an explicit refresh at the end."""
    try:
        time_str = f"as of {fetch_time}" if fetch_time else "as of --:--"
        print(f"Updating display: {int(commute_mins)} min, {time_str}")
        magtag.set_text("", 0, auto_refresh=False)
        magtag.set_text(f"{int(commute_mins)} min", 1, auto_refresh=False)
        magtag.set_text("--:--", 2, auto_refresh=False)
        magtag.set_text(time_str, 3, auto_refresh=False)
        if safe_refresh():
            print("Display refreshed.")
        else:
            print("Display refresh failed — will retry next cycle")
    except Exception as e:
        print(f"Display update error: {e}")
        traceback.print_exception(e)


# ==========================================
# MAIN LOOP
# ==========================================

interval = int(os.getenv("UPDATE_INTERVAL", "600"))

while True:
    commute_mins = get_commute_time()
    fetch_time = get_fetch_time()

    if commute_mins is not None and commute_mins > 0:
        print(f"Current commute: {commute_mins:.1f} minutes")
        update_display(commute_mins, fetch_time)
        update_leds(commute_mins)
    elif commute_mins == 0:
        print("API returned 0 minutes — treating as error")
        show_error("Bad Data", "--:--", fetch_time)
    else:
        print("API error — no commute time returned")
        show_error("API Error", "--:--", fetch_time)

    print(f"Sleeping for {interval} seconds...")
    time.sleep(interval)
