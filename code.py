import board
import displayio
import adafruit_displayio_sh1106
import gifio
import time
import gc
import os
import digitalio
from adafruit_display_text import label
import terminalio
import busio
import wifi
import socketpool
import adafruit_ntp
import rtc

# Release any existing displays
displayio.release_displays()

# Configure SPI with explicit pins for ESP32-S2 Mini
spi = busio.SPI(clock=board.IO36, MOSI=board.IO35)
tft_cs = board.IO7   # Chip select (display)
tft_dc = board.IO3   # Data/command (display)
tft_reset = board.IO5  # Reset (display)

# Initialize SPI bus at 1MHz
display_bus = displayio.FourWire(
    spi,
    command=tft_dc,
    chip_select=tft_cs,
    reset=tft_reset,
    baudrate=1000000
)

# Initialize SH1106 display with 130 width to account for the buffer
WIDTH = 130  # Use 130 instead of 128 to include the full buffer
HEIGHT = 64
display = adafruit_displayio_sh1106.SH1106(
    display_bus,
    width=WIDTH,
    height=HEIGHT
)

# Create a main group that applies the 2-pixel offset to ALL content
main_group = displayio.Group(x=2, y=0)
display.root_group = main_group

# Physical button setup
class PhysicalButton:
    def __init__(self, button_pin, pull=digitalio.Pull.UP):
        self.button = digitalio.DigitalInOut(button_pin)
        self.button.direction = digitalio.Direction.INPUT
        self.button.pull = pull
        self.last_state = self.button.value
        print(f"Button on {button_pin} initialized")

    def pressed(self):
        current_state = self.button.value
        # Detect falling edge (button press) for pull-up configuration
        # For pull-up: pressed = False (LOW), not pressed = True (HIGH)
        pressed = (self.last_state is True) and (current_state is False)
        self.last_state = current_state
        return pressed

# Set up physical buttons
try:
    next_button = PhysicalButton(board.IO12)
    prev_button = PhysicalButton(board.IO11)
    mode_button = PhysicalButton(board.IO2)  # New mode button
except Exception as e:
    print(f"Button init error: {e}")

# Button press cooldown to prevent spam (in seconds)
BUTTON_COOLDOWN = 0.5
last_button_press = 0

# Mode state
current_mode = "gif"  # Start in GIF mode

# WiFi and time setup
try:
    # Get WiFi details from settings.toml
    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
    tz_offset_str = os.getenv("CIRCUITPY_TZ_OFFSET", "0")  # Default to 0 if not set
    
    # Convert timezone offset to integer
    tz_offset = int(tz_offset_str)
    
    if ssid and password:
        wifi.radio.connect(ssid, password)
        pool = socketpool.SocketPool(wifi.radio)
        ntp = adafruit_ntp.NTP(pool, tz_offset=tz_offset)
        rtc.RTC().datetime = ntp.datetime
        print(f"Time synchronized via NTP (Timezone offset: {tz_offset} hours)")
    else:
        print("WiFi credentials not found in settings.toml")
except Exception as e:
    print(f"Failed to sync time: {e}")

# Create clock display elements once (not every second)
time_label = label.Label(terminalio.FONT, text="00:00:00", color=0xFFFFFF)
time_label.x = 10
time_label.y = HEIGHT // 2 - 10

date_label = label.Label(terminalio.FONT, text="YYYY-MM-DD", color=0xFFFFFF)
date_label.x = 10
date_label.y = HEIGHT // 2 + 10

clock_group = displayio.Group()
clock_group.append(time_label)
clock_group.append(date_label)

def update_clock_display():
    """Update the clock display with current time (no flicker)"""
    try:
        now = time.localtime()
        
        # Format time as HH:MM:SS
        hour_12 = now.tm_hour % 12
        if hour_12 == 0:
            hour_12 = 12
        period = "AM" if now.tm_hour < 12 else "PM"
        time_str = "{:02d}:{:02d}:{:02d} {}".format(hour_12, now.tm_min, now.tm_sec, period)
        time_label.text = time_str
        
        # Format date as YYYY-MM-DD
        date_str = "{:04d}-{:02d}-{:02d}".format(now.tm_year, now.tm_mon, now.tm_mday)
        date_label.text = date_str
        
    except Exception as e:
        print(f"Error updating clock: {e}")

def switch_mode():
    """Switch between GIF and clock modes"""
    global current_mode
    if current_mode == "gif":
        current_mode = "clock"
        display.root_group = clock_group
        update_clock_display()  # Update immediately when switching to clock
        print("Switched to Clock mode")
    else:
        current_mode = "gif"
        display.root_group = main_group
        print("Switched to GIF mode")

# Function to play A0.gif for the same duration as the original wait
def show_interstitial():
    interstitial_path = "/gifs/z9loader.gif"

    try:
        # Check if A0.gif exists
        try:
            odg = gifio.OnDiskGif(interstitial_path)
        except:
            # If A0.gif doesn't exist, fall back to text
            show_please_wait()
            return

        face = displayio.TileGrid(
            odg.bitmap,
            pixel_shader=displayio.ColorConverter(
                input_colorspace=displayio.Colorspace.L8
            ),
            x=0,
            y=0
        )

        while len(main_group) > 0:
            main_group.pop()
        main_group.append(face)

        # Play A0.gif for approximately 2 seconds (same as original wait)
        start_time = time.monotonic()
        next_delay = odg.next_frame()
        frame_start = start_time

        while time.monotonic() - start_time < 2.0:  # Play for 2 seconds
            elapsed = time.monotonic() - frame_start
            if elapsed >= next_delay:
                frame_start = time.monotonic()
                next_delay = odg.next_frame()
            else:
                time.sleep(0.001)

        odg.deinit()
        gc.collect()

    except Exception as e:
        print(f"Error playing interstitial: {e}")
        # Fallback to text if there's an error
        show_please_wait()

# Keep the original please wait function as fallback
def show_please_wait():
    while len(main_group) > 0:
        main_group.pop()

    text = "Please wait..."
    text_area = label.Label(terminalio.FONT, text=text, color=0xFFFFFF)
    text_area.x = 10
    text_area.y = HEIGHT // 2

    main_group.append(text_area)
    time.sleep(2.0)

# Enhanced button checking function
def button_pressed():
    global last_button_press

    current_time = time.monotonic()
    if current_time - last_button_press < BUTTON_COOLDOWN:
        return False, None

    try:
        next_pressed = next_button.pressed()
        prev_pressed = prev_button.pressed()
        mode_pressed = mode_button.pressed()

        # Debug output (comment out in production)
        # print(f"Buttons - Next: {next_pressed}, Prev: {prev_pressed}, Mode: {mode_pressed}")

        if mode_pressed:
            last_button_press = current_time
            return True, "mode"
        elif next_pressed and not prev_pressed:
            last_button_press = current_time
            return True, "next"
        elif prev_pressed and not next_pressed:
            last_button_press = current_time
            return True, "previous"

    except Exception as e:
        print(f"Button read error: {e}")

    return False, None

def get_gif_files():
    gif_dir = "/gifs"
    files = []

    try:
        os.mkdir(gif_dir)
    except OSError:
        pass

    for file in os.listdir(gif_dir):
        if file.lower().endswith('.gif'):
            files.append(f"{gif_dir}/{file}")

    return sorted(files)

def play_gif(gif_path):
    try:
        odg = gifio.OnDiskGif(gif_path)

        face = displayio.TileGrid(
            odg.bitmap,
            pixel_shader=displayio.ColorConverter(
                input_colorspace=displayio.Colorspace.L8
            ),
            x=(128 - odg.bitmap.width) // 2 if odg.bitmap.width < 128 else 0,
            y=(64 - odg.bitmap.height) // 2 if odg.bitmap.height < 64 else 0
        )

        while len(main_group) > 0:
            main_group.pop()
        main_group.append(face)

        next_delay = odg.next_frame()
        start_time = time.monotonic()

        while True:
            pressed, direction = button_pressed()
            if pressed:
                print(f"Button pressed - {direction}")
                return direction

            elapsed = time.monotonic() - start_time
            if elapsed >= next_delay:
                start_time = time.monotonic()
                next_delay = odg.next_frame()
            else:
                time.sleep(0.001)

        odg.deinit()
        gc.collect()
        return True

    except Exception as e:
        print(f"Error playing {gif_path}: {e}")
        return "error"

def show_error(message):
    while len(main_group) > 0:
        main_group.pop()

    text_area = label.Label(terminalio.FONT, text=message, color=0xFFFFFF)
    text_area.x = 10
    text_area.y = HEIGHT // 2

    main_group.append(text_area)
    time.sleep(2)

# Main loop
gif_files = get_gif_files()
current_gif_index = 0

if not gif_files:
    show_error("No GIFs in /gifs")
    while True:
        time.sleep(1)
else:
    print(f"Found {len(gif_files)} GIFs")

    # Initial setup period for buttons
    print("Initializing buttons...")
    time.sleep(1)

    while True:
        try:
            if current_mode == "gif":
                print(f"Playing GIF {current_gif_index + 1}/{len(gif_files)}")

                result = play_gif(gif_files[current_gif_index])

                if result == "next":
                    show_interstitial()
                    current_gif_index = (current_gif_index + 1) % len(gif_files)
                    print(f"Switching to next GIF: {current_gif_index + 1}/{len(gif_files)}")
                elif result == "previous":
                    show_interstitial()
                    current_gif_index = (current_gif_index - 1) % len(gif_files)
                    print(f"Switching to previous GIF: {current_gif_index + 1}/{len(gif_files)}")
                elif result == "mode":
                    switch_mode()
                else:
                    show_interstitial()
                    current_gif_index = (current_gif_index + 1) % len(gif_files)
                    print(f"Error with current GIF, trying next: {current_gif_index + 1}/{len(gif_files)}")
            else:
                # Clock mode - update time every second without flickering
                update_clock_display()
                time.sleep(1)
                
                # Check for mode button press in clock mode
                pressed, direction = button_pressed()
                if pressed and direction == "mode":
                    switch_mode()

        except Exception as e:
            print(f"Fatal error in main loop: {e}")
            show_error("Fatal error, resetting")
            current_gif_index = 0
