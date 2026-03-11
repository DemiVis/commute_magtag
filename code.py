"""
MagTag Commute Dashboard
------------------------
Fetches Google Routes API commute times and displays them on the top half
of the E-Ink display. Changes MagTag NeoPixels from Green (Good) to Red (Bad).
Leaves bottom half available for future expansions (e.g., train schedules).

# TODO: Update dependencies once code is actually working!
Dependencies (put in /lib):
- adafruit_requests
- adafruit_display_text
- neopixel
- adafruit_connection_manager

Settings (put in /settings.toml):
- WIFI_SSID
- WIFI_PASSWORD
- GOOGLE_ROUTES_API_KEY
"""

import os
import time
import wifi
import socketpool
import ssl
import board
import neopixel
import displayio
import terminalio
import adafruit_requests
from adafruit_display_text import label

# ==========================================
# CONFIGURATION
# ==========================================
# in settings.toml, make sure to have at least the following configs:
# WiFi Details 
# WIFI_SSID = "MyWiFi"
# WIFI_PASSWORD = "SuperSecurePassword"

# Google Routes private API
# GOOGLE_ROUTES_API_KEY = "abcdefghijklmnopqrstuvwxyz"

# Commute Addresses (can be exact addresses or Google Maps Plus Codes)
# ORIGIN_ADDRESS = "1600 Amphitheatre Parkway, Mountain View, CA"
# DEST_ADDRESS = "1800 Amphitheatre Parkway, Mountain View, CA"

# Thresholds for LED color mapping (in minutes)
# GOOD_COMMUTE or less = 100% Green
# BAD_COMMUTE or more = 100% Red
# GOOD_COMMUTE_MINS = 45
# BAD_COMMUTE_MINS = 70

# How often to check the API (in seconds) - Default: 10 minutes
# Note: Google API charges per request after free tier, 10 mins = 144 req/day.
# UPDATE_INTERVAL = 180

# Adafruit IO for time gathering
# ADAFRUIT_AIO_USERNAME = "ladyada"
# ADAFRUIT_AIO_KEY      = "sasdfkjasdfjasdffsadf"
# TIMEZONE="America/New_York"

# ==========================================
# HARDWARE SETUP
# ==========================================

# Initialize NeoPixels (MagTag has 4 NeoPixels)
pixels = neopixel.NeoPixel(board.NEOPIXEL, 4, brightness=0.1, auto_write=True)
pixels.fill((0, 0, 0)) # Start off

# Initialize Display
display = board.DISPLAY

# Create Display Groups for modularity
main_group = displayio.Group()

# Create a white background so black text is clearly visible
bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0xFFFFFF # White background
bg_sprite = displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette)
main_group.append(bg_sprite)

top_half_group = displayio.Group(x=0, y=0)    # Top 64 pixels for Commute
bottom_half_group = displayio.Group(x=0, y=64) # Bottom 64 pixels for Train (Future)

main_group.append(top_half_group)
main_group.append(bottom_half_group)
display.root_group = main_group

# Setup Commute UI Elements (Top Half)
# Simple text-based "Icon" to avoid needing external BMP files.
# You can replace this with a displayio.TileGrid bitmap later.
icon_label = label.Label(terminalio.FONT, text="[CAR]", color=0x000000, scale=2)
icon_label.x = 10
icon_label.y = 32
top_half_group.append(icon_label)

time_label = label.Label(terminalio.FONT, text="Loading...", color=0x000000, scale=3)
time_label.x = 90
time_label.y = 32
top_half_group.append(time_label)

# Setup Future Train UI Elements (Bottom Half)
# Placeholder for your future implementation
train_label = label.Label(terminalio.FONT, text="Train: --:--", color=0x000000, scale=2)
train_label.x = 10
train_label.y = 32
bottom_half_group.append(train_label)

# ==========================================
# NETWORK SETUP
# ==========================================

def connect_wifi():
    print("Connecting to WiFi...")
    try:
        wifi.radio.connect(os.getenv("WIFI_SSID"), os.getenv("WIFI_PASSWORD"))
        print(f"Connected! IP: {wifi.radio.ipv4_address}")
    except Exception as e:
        print(f"WiFi Connection Error: {e}")
        time.sleep(10)
        # Soft reset if wifi fails consistently
        import microcontroller
        microcontroller.reset()

connect_wifi()
pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_commute_time():
    """Fetches commute duration from Google Routes API v2."""
    api_key = os.getenv("GOOGLE_ROUTES_API_KEY")
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration"
    }

    payload = {
        "origin": {"address": os.getenv("ORIGIN_ADDRESS")},
        "destination": {"address": os.getenv("DEST_ADDRESS")},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE"
    }

    print("Fetching route data...")
    try:
        response = requests.post(url, json=payload, headers=headers)
        data = response.json()
        response.close()

        # Google returns duration as a string with 's' at the end, e.g., "1800s"
        duration_str = data.get("routes", [{}])[0].get("duration", "0s")
        duration_seconds = int(duration_str.replace("s", ""))
        return duration_seconds / 60.0 # Convert to minutes

    except Exception as e:
        print(f"Error fetching API: {e}")
        return None

def update_leds(commute_mins):
    """Linearly interpolates LED colors between Green (Good) and Red (Bad)."""
    # Clamp values to our defined good/bad limits
    GOOD_COMMUTE_MINS = float(os.getenv("GOOD_COMMUTE_MINS"))
    BAD_COMMUTE_MINS = float(os.getenv("BAD_COMMUTE_MINS"))

    if commute_mins <= GOOD_COMMUTE_MINS:
        ratio = 0.0
    elif commute_mins >= BAD_COMMUTE_MINS:
        ratio = 1.0
    else:
        # Calculate sliding scale ratio (0.0 to 1.0)
        ratio = (commute_mins - GOOD_COMMUTE_MINS) / (BAD_COMMUTE_MINS - GOOD_COMMUTE_MINS)

    # Calculate RGB: Green goes down as ratio goes up, Red goes up as ratio goes up
    red = int(ratio * 255)
    green = int((1.0 - ratio) * 255)
    blue = 0

    pixels.fill((red, green, blue))

def safe_refresh():
    """Wait for e-ink cooldown and refresh, retrying if needed."""
    time_to_refresh =  display.time_to_refresh
    print(f"Display requires waiting {time_to_refresh}sec")
    time.sleep(time_to_refresh+2)
    try:
        display.refresh()
    except RuntimeError:
        # time_to_refresh was not accurate; wait and retry
        print("Display refresh failed, waiting 3min and trying again..")
        time.sleep(180)
        display.refresh()

def update_display_text(commute_mins):
    """Updates the E-Ink display."""
    time_label.text = f"{int(commute_mins)} mins"
    safe_refresh()


def get_wall_clock_time():
    """ Get the current time from AdafruitIO"""
    aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
    aio_key = os.getenv("ADAFRUIT_AIO_KEY")
    timezone = os.getenv("TIMEZONE")
    TIME_URL = f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime?x-aio-key={aio_key}&tz={timezone}"
    TIME_URL += "&fmt=%25Y-%25m-%25d+%25H%3A%25M%3A%25S.%25L+%25j+%25u+%25z+%25Z"
    print("Fetching text from", TIME_URL)
    response = requests.get(TIME_URL)
    # print("-" * 40)
    # print(response.text)
    # print("-" * 40)
    return response.text

# ==========================================
# MAIN LOOP
# ==========================================

while True:
    # Ensure WiFi is still connected
    if not wifi.radio.ipv4_address:
        connect_wifi()

    # Fetch current wall clock time 
    print(get_wall_clock_time())

    # Fetch the commute data
    commute_mins = get_commute_time()

    # Update UI and LEDs if data was fetched successfully
    if commute_mins is not None:
        print(f"Current commute: {commute_mins:.1f} minutes")
        update_leds(commute_mins)
        update_display_text(commute_mins)
    else:
        time_label.text = "API Error"
        pixels.fill((0, 0, 255)) # Blue indicates an error state
        safe_refresh()

    # Wait until the next update interval
    interval = int(os.getenv("UPDATE_INTERVAL", "600"))
    print(f"Sleeping for {interval} seconds...")
    time.sleep(interval)
