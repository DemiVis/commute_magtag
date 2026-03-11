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
- CIRCUITPY_WIFI_SSID       WiFi network name (read by adafruit_portalbase)
- CIRCUITPY_WIFI_PASSWORD   WiFi password (read by adafruit_portalbase)
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
import displayio
from adafruit_magtag.magtag import MagTag

BRIGHTNESS_PER = 10  # NeoPixel brightness percentage (0-100)
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
magtag.graphics.splash.append(car_group)

# Train icon: 64×36 px, vertically centered on bottom half (center y=96 → top y=78)
train_group = displayio.Group(scale=ICON_SCALE, x=10, y=78)
train_group.append(make_icon(TRAIN_PIXELS))
magtag.graphics.splash.append(train_group)

# ==========================================
# NETWORK SETUP
# ==========================================

magtag.network.connect()

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_commute_time():
    """Fetches commute duration from Google Routes API v2. Returns minutes or None."""
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY")
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration",
    }

    payload = {
        "origin": {"address": os.getenv("ORIGIN_ADDRESS")},
        "destination": {"address": os.getenv("DEST_ADDRESS")},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }

    print("Fetching route data...")
    try:
        response = magtag.network.requests.post(url, json=payload, headers=headers)
        data = response.json()
        response.close()

        # Google returns duration as a string like "1800s"
        duration_str = data.get("routes", [{}])[0].get("duration", "0s")
        duration_seconds = int(duration_str.replace("s", ""))
        return duration_seconds / 60.0

    except Exception as e:
        print(f"Error fetching API: {e}")
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
    print("Fetching time...")
    try:
        response = magtag.network.requests.get(url)
        result = response.text.strip()
        response.close()
        return result
    except Exception as e:
        print(f"Error fetching time: {e}")
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
    """Updates the E-Ink display. Only the final set_text triggers a refresh."""
    time_str = f"as of {fetch_time}" if fetch_time else "as of --:--"
    magtag.set_text("", 0, auto_refresh=False)
    magtag.set_text(f"{int(commute_mins)} min", 1, auto_refresh=False)
    magtag.set_text("--:--", 2, auto_refresh=False)
    magtag.set_text(time_str, 3, auto_refresh=True)


# ==========================================
# MAIN LOOP
# ==========================================

interval = int(os.getenv("UPDATE_INTERVAL", "600"))

while True:
    commute_mins = get_commute_time()
    fetch_time = get_fetch_time()

    if commute_mins is not None:
        print(f"Current commute: {commute_mins:.1f} minutes")
        update_leds(commute_mins)
        update_display(commute_mins, fetch_time)
    else:
        time_str = f"upd: {fetch_time}" if fetch_time else "upd: --:--"
        magtag.peripherals.neopixels.fill((0, 0, 255))  # Blue = error state
        magtag.peripherals.neopixel_disable = False
        magtag.set_text("", 0, auto_refresh=False)
        magtag.set_text("API Error", 1, auto_refresh=False)
        magtag.set_text("--:--", 2, auto_refresh=False)
        magtag.set_text(time_str, 3, auto_refresh=True)

    print(f"Sleeping for {interval} seconds...")
    time.sleep(interval)
