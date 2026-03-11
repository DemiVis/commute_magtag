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

Constants (edit in code.py):
- BRIGHTNESS_PER            NeoPixel brightness, 0-100 percent (default: 10)
"""

import os
import time
from adafruit_magtag.magtag import MagTag

BRIGHTNESS_PER = 10  # NeoPixel brightness percentage (0-100)

# ==========================================
# HARDWARE & DISPLAY SETUP
# ==========================================

magtag = MagTag()

# Text slot 0: Car icon label (top-left)
magtag.add_text(
    text_position=(10, 32),
    text_scale=2,
    text_color=0x000000,
)

# Text slot 1: Commute time (top-right, large)
magtag.add_text(
    text_position=(100, 32),
    text_scale=3,
    text_color=0x000000,
)

# Text slot 2: Bottom half placeholder for future train info
magtag.add_text(
    text_position=(10, 96),
    text_scale=2,
    text_color=0x000000,
)

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


def update_display(commute_mins):
    """Updates the E-Ink display. Only the final set_text triggers a refresh."""
    magtag.set_text("[CAR]", 0, auto_refresh=False)
    magtag.set_text(f"{int(commute_mins)} min", 1, auto_refresh=False)
    magtag.set_text("Train: --:--", 2, auto_refresh=True)


# ==========================================
# MAIN LOOP
# ==========================================

interval = int(os.getenv("UPDATE_INTERVAL", "600"))

while True:
    commute_mins = get_commute_time()

    if commute_mins is not None:
        print(f"Current commute: {commute_mins:.1f} minutes")
        update_leds(commute_mins)
        update_display(commute_mins)
    else:
        magtag.peripherals.neopixels.fill((0, 0, 255))  # Blue = error state
        magtag.peripherals.neopixel_disable = False
        magtag.set_text("[CAR]", 0, auto_refresh=False)
        magtag.set_text("API Error", 1, auto_refresh=False)
        magtag.set_text("Train: --:--", 2, auto_refresh=True)

    print(f"Sleeping for {interval} seconds...")
    time.sleep(interval)
