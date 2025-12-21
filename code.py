import board
import displayio
import adafruit_displayio_sh1106
import gifio
import time
import gc
import os
import digitalio
import pwmio
from adafruit_display_text import label
import terminalio
import busio
import wifi
import socketpool
import adafruit_ntp
import rtc
import asyncio
import analogio  # Added for analog reading
import array  # Added for storing wave data

# Release any existing displays
displayio.release_displays()

# Clean up macOS metadata files
def cleanup_macos_files(directory):
    """Remove macOS ._ files from the specified directory"""
    try:
        if directory in os.listdir('/'):
            files = os.listdir(directory)
            removed_count = 0
            for file in files:
                if file.startswith('._'):
                    try:
                        full_path = directory + '/' + file
                        os.remove(full_path)
                        print(f"Removed macOS file: {full_path}")
                        removed_count += 1
                    except Exception as e:
                        print(f"Error removing {file}: {e}")
            if removed_count > 0:
                print(f"Cleaned up {removed_count} macOS files from {directory}")
    except Exception as e:
        print(f"Error cleaning up {directory}: {e}")

# Clean up both directories
cleanup_macos_files('/gifs')
cleanup_macos_files('/wavs')

fled = digitalio.DigitalInOut(board.IO14)
fled.direction = digitalio.Direction.OUTPUT

uvled = digitalio.DigitalInOut(board.IO10)
uvled.direction = digitalio.Direction.OUTPUT

fled.value = False
uvled.value = False

# Initialize PWM once and keep it alive
pwm = pwmio.PWMOut(board.IO18, frequency=40000, duty_cycle=0)
DELAY_COUNT = 65  # Optimal delay for Lolin S2 Mini

# Initialize analog input for microphone/IO12
mic = analogio.AnalogIn(board.IO12)

# Configure SPI with explicit pins for ESP32-S2 Mini
spi = busio.SPI(clock=board.IO7, MOSI=board.IO11)
tft_cs = board.IO13   # Chip select (display)
tft_dc = board.IO5   # Data/command (display)
tft_reset = board.IO3  # Reset (display)

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
display.rotation = 90

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
    next_button = PhysicalButton(board.IO8)
    prev_button = PhysicalButton(board.IO6)
    mode_button = PhysicalButton(board.IO2)  # New mode button
except Exception as e:
    print(f"Button init error: {e}")

# Button press cooldown to prevent spam (in seconds)
BUTTON_COOLDOWN = 0.5
last_button_press = 0

# Mode state - Removed "wav" and "wav_gif", added "mic"
current_mode = "gif"  # Start in GIF mode

# LED Control Mode State
# 0: LED1 on, LED2 off
# 1: LED2 on, LED1 off  
# 2: Both on
# 3: Both off
# 4: Flashing between LED1 and LED2
led_control_state = 0
flash_state = 0
last_flash_time = 0
FLASH_INTERVAL = 0.5  # seconds between flash changes

# Wave display parameters for Mic mode (rotated 90 degrees)
# Since display is rotated 90 degrees, we need to adjust dimensions
WAVE_WIDTH = 64  # Display height becomes width after rotation
WAVE_HEIGHT = 128  # Display width becomes height after rotation
WAVE_BUFFER_SIZE = 64  # Number of samples to display horizontally
wave_buffer = array.array('H', [0] * WAVE_BUFFER_SIZE)  # Store samples as unsigned shorts
wave_index = 0  # Current position in buffer

# Calibration parameters for wave display
AMP_VAL = 1600  # Amplification value (adjust for sensitivity)
Y_OFFSET = 944  # DC offset to center wave (adjust based on your signal)
# For ESP32-S2, analog values range 0-65535 (16-bit)

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

def turn_off_all_leds():
    """Turn off both LEDs - used when switching to non-LED modes"""
    fled.value = False
    uvled.value = False

def stop_audio():
    """Stop audio playback by setting duty cycle to 0"""
    pwm.duty_cycle = 0

async def play_gif_looping(gif_path, stop_event):
    """Play GIF with looping - continues until stop_event is set"""
    odg = None
    try:
        odg = gifio.OnDiskGif(gif_path)
        face = displayio.TileGrid(
            odg.bitmap,
            pixel_shader=displayio.ColorConverter(
                input_colorspace=displayio.Colorspace.L8
            ),
            x=(64 - odg.bitmap.width) // 2 if odg.bitmap.width < 64 else 0,
            y=(128 - odg.bitmap.height) // 2 if odg.bitmap.height < 128 else 0
        )

        # Clear and set up display
        while len(main_group) > 0:
            main_group.pop()
        main_group.append(face)

        next_delay = odg.next_frame()
        last_frame_time = time.monotonic()

        while not stop_event.is_set():
            current_time = time.monotonic()
            
            # Check if it's time for next frame
            if current_time - last_frame_time >= next_delay:
                last_frame_time = current_time
                try:
                    next_delay = odg.next_frame()
                except EOFError:
                    # GIF ended - restart
                    odg.deinit()
                    odg = gifio.OnDiskGif(gif_path)
                    next_delay = odg.next_frame()
            
            # Yield with the optimized timing you found
            await asyncio.sleep(0.09)  # Your optimized balance

        if odg:
            odg.deinit()
        gc.collect()

    except Exception as e:
        print(f"Error playing {gif_path}: {e}")
        if odg:
            odg.deinit()

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

# Create LED control display elements
led_title = label.Label(terminalio.FONT, text="LED CONTROL", color=0xFFFFFF)
led_title.x = 10
led_title.y = 10

fled_label = label.Label(terminalio.FONT, text="LED1: OFF", color=0xFFFFFF)
fled_label.x = 10
fled_label.y = 30

uvled_label = label.Label(terminalio.FONT, text="LED2: OFF", color=0xFFFFFF)
uvled_label.x = 10
uvled_label.y = 50

led_control_group = displayio.Group()
led_control_group.append(led_title)
led_control_group.append(fled_label)
led_control_group.append(uvled_label)

# Create bitmap for wave display
wave_bitmap = displayio.Bitmap(WAVE_WIDTH, WAVE_HEIGHT, 2)  # 2 colors
wave_palette = displayio.Palette(2)
wave_palette[0] = 0x000000  # Black background
wave_palette[1] = 0xFFFFFF  # White wave
wave_tilegrid = displayio.TileGrid(wave_bitmap, pixel_shader=wave_palette)

# Create Mic mode group with just the wave display
mic_group = displayio.Group()
mic_group.append(wave_tilegrid)

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

def clear_wave_display():
    """Clear the wave display"""
    for x in range(WAVE_WIDTH):
        for y in range(WAVE_HEIGHT):
            wave_bitmap[x, y] = 0

def update_wave_display():
    """Update the wave display with new samples - similar to your Arduino code"""
    global wave_index
    
    # Read analog value from IO12
    sample = mic.value
    
    # Apply your formula: ((sample * ampVal) / 64) - yoffset
    # Adjusted for 16-bit ADC (0-65535) and display rotation
    # Note: display is rotated 90 degrees, so we're drawing vertical lines
    new_y = int(((sample * AMP_VAL) // 64) - (Y_OFFSET // 512))
    
    # Map to display height (128 pixels tall after rotation)
    # Center around middle (64) with some scaling
    new_y = (new_y // 512) + 64  # Adjust these values for your signal
    
    # Clamp to display bounds
    new_y = max(0, min(WAVE_HEIGHT - 1, new_y))
    
    # Store in buffer
    wave_buffer[wave_index] = new_y
    
    # Clear the vertical line at current position
    for y in range(WAVE_HEIGHT):
        wave_bitmap[wave_index, y] = 0
    
    # Draw the new sample point
    wave_bitmap[wave_index, new_y] = 1
    
    # Connect to previous sample with a line
    if wave_index > 0:
        prev_y = wave_buffer[wave_index - 1]
        last_y = wave_buffer[wave_index]
        
        # Draw vertical line between previous and current point
        # Since we're drawing vertical lines column by column, we connect points
        # in the same column (vertical lines between samples in adjacent columns)
        # Actually, we need to think differently for rotated display...
        
        # For rotated display (90 degrees), we're drawing column by column
        # Each column gets a single point at new_y
        # To create a continuous wave, we need to draw lines between columns
        if wave_index > 0:
            prev_x = wave_index - 1
            prev_y_val = wave_buffer[prev_x]
            
            # Draw line between (prev_x, prev_y) and (wave_index, new_y)
            # Simple line drawing algorithm
            if abs(new_y - prev_y_val) > 1:
                x0, y0 = prev_x, prev_y_val
                x1, y1 = wave_index, new_y
                
                # Bresenham line algorithm for better line drawing
                dx = abs(x1 - x0)
                dy = abs(y1 - y0)
                sx = 1 if x0 < x1 else -1
                sy = 1 if y0 < y1 else -1
                err = dx - dy
                
                while True:
                    wave_bitmap[x0, y0] = 1
                    if x0 == x1 and y0 == y1:
                        break
                    e2 = 2 * err
                    if e2 > -dy:
                        err -= dy
                        x0 += sx
                    if e2 < dx:
                        err += dx
                        y0 += sy
    
    # Update index for next sample
    wave_index = (wave_index + 1) % WAVE_WIDTH
    
    # If we wrapped around, clear the next position we're about to write to
    if wave_index == 0:
        # Clear the first column for the next cycle
        for y in range(WAVE_HEIGHT):
            wave_bitmap[0, y] = 0

def switch_mode():
    """Switch between GIF, clock, LED control, and Mic modes"""
    global current_mode, wave_index
    
    if current_mode == "gif":
        current_mode = "clock"
        display.root_group = clock_group
        turn_off_all_leds()
        stop_audio()
        update_clock_display()
        print("Switched to Clock mode")
    elif current_mode == "clock":
        current_mode = "led_control"
        display.root_group = led_control_group
        stop_audio()
        update_led_display()
        print("Switched to LED Control mode")
    elif current_mode == "led_control":
        current_mode = "mic"
        display.root_group = mic_group
        turn_off_all_leds()
        stop_audio()
        # Clear and initialize wave display
        clear_wave_display()
        wave_index = 0
        print("Switched to Mic mode")
    else:
        current_mode = "gif"
        display.root_group = main_group
        turn_off_all_leds()
        stop_audio()
        print("Switched to GIF mode")

def update_led_display():
    """Update the LED control display based on current state"""
    global flash_state, last_flash_time
    
    if led_control_state == 0:  # LED1 on, LED2 off
        fled_label.text = "LED1: ON"
        uvled_label.text = "LED2: OFF"
        fled.value = True
        uvled.value = False
        flash_state = 0
    elif led_control_state == 1:  # LED2 on, LED1 off
        fled_label.text = "LED1: OFF" 
        uvled_label.text = "LED2: ON"
        fled.value = False
        uvled.value = True
        flash_state = 0
    elif led_control_state == 2:  # Both on
        fled_label.text = "LED1: ON"
        uvled_label.text = "LED2: ON"
        fled.value = True
        uvled.value = True
        flash_state = 0
    elif led_control_state == 3:  # Both off
        fled_label.text = "LED1: OFF"
        uvled_label.text = "LED2: OFF"
        fled.value = False
        uvled.value = False
        flash_state = 0
    elif led_control_state == 4:  # Flashing mode
        fled_label.text = "LED1: FLASHING"
        uvled_label.text = "LED2: FLASHING"
        # Flash state will be handled in the main loop
        last_flash_time = time.monotonic()

def handle_flashing():
    """Handle the flashing mode - called repeatedly in main loop"""
    global flash_state, last_flash_time
    
    current_time = time.monotonic()
    if current_time - last_flash_time >= FLASH_INTERVAL:
        if flash_state == 0:
            # LED1 on, LED2 off
            fled.value = True
            uvled.value = False
            flash_state = 1
        else:
            # LED2 on, LED1 off
            fled.value = False
            uvled.value = True
            flash_state = 0
        last_flash_time = current_time

def switch_led_state(direction):
    """Switch between LED states in LED control mode"""
    global led_control_state
    if direction == "next":
        led_control_state = (led_control_state + 1) % 5
    elif direction == "previous":
        led_control_state = (led_control_state - 1) % 5
    
    update_led_display()
    print(f"LED State: {led_control_state}")

def show_please_wait():
    """Show a simple 'please wait' message"""
    while len(main_group) > 0:
        main_group.pop()

    text = "Just a \nsec..."
    text_area = label.Label(terminalio.FONT, text=text, color=0xFFFFFF)
    text_area.x = 10
    text_area.y = HEIGHT-15

    main_group.append(text_area)
    time.sleep(0.5)

def button_pressed():
    global last_button_press

    current_time = time.monotonic()
    if current_time - last_button_press < BUTTON_COOLDOWN:
        return False, None

    try:
        next_pressed = next_button.pressed()
        prev_pressed = prev_button.pressed()
        mode_pressed = mode_button.pressed()

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
    """Get all GIF files from /gifs directory"""
    gif_dir = "/gifs"
    files = []

    try:
        os.mkdir(gif_dir)
    except OSError:
        pass

    for file in os.listdir(gif_dir):
        if file.lower().endswith('.gif') and not file.startswith('._'):
            files.append(f"{gif_dir}/{file}")

    return sorted(files)

async def play_gif_async_standalone(gif_path):
    """Play GIF with seamless looping using asyncio"""
    stop_event = asyncio.Event()
    
    try:
        odg = gifio.OnDiskGif(gif_path)

        face = displayio.TileGrid(
            odg.bitmap,
            pixel_shader=displayio.ColorConverter(
                input_colorspace=displayio.Colorspace.L8
            ),
            x=(64 - odg.bitmap.width) // 2 if odg.bitmap.width < 64 else 0,
            y=(128 - odg.bitmap.height) // 2 if odg.bitmap.height < 128 else 0
        )

        while len(main_group) > 0:
            main_group.pop()
        main_group.append(face)

        next_delay = odg.next_frame()
        start_time = time.monotonic()

        while not stop_event.is_set():
            pressed, direction = button_pressed()
            if pressed:
                stop_event.set()
                return direction

            elapsed = time.monotonic() - start_time
            if elapsed >= next_delay:
                start_time = time.monotonic()
                try:
                    next_delay = odg.next_frame()
                except EOFError:
                    # GIF ended - restart it for seamless looping
                    odg.deinit()
                    odg = gifio.OnDiskGif(gif_path)
                    next_delay = odg.next_frame()
            
            await asyncio.sleep(0.001)

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

# Initialize files - only GIFs needed now
gif_files = get_gif_files()
current_gif_index = 0

# Calibration helper - you might need to adjust these values
def calibrate_mic():
    """Take a few readings to help calibrate the Y_OFFSET"""
    print("Calibrating microphone...")
    samples = []
    for i in range(100):
        samples.append(mic.value)
        time.sleep(0.01)
    
    avg = sum(samples) // len(samples)
    print(f"Average ambient reading: {avg}")
    print(f"Suggested Y_OFFSET: {avg} (adjust in code)")
    return avg

# Optional: Uncomment to run calibration when starting
calibrate_mic()

# Main async loop
async def main_async_loop():
    global current_gif_index, current_mode
    
    while True:
        try:
            if current_mode == "gif":
                if gif_files:
                    print(f"Playing GIF {current_gif_index + 1}/{len(gif_files)}")

                    result = await play_gif_async_standalone(gif_files[current_gif_index])

                    if result == "next":
                        show_please_wait()
                        current_gif_index = (current_gif_index + 1) % len(gif_files)
                        print(f"Switching to next GIF: {current_gif_index + 1}/{len(gif_files)}")
                    elif result == "previous":
                        show_please_wait()
                        current_gif_index = (current_gif_index - 1) % len(gif_files)
                        print(f"Switching to previous GIF: {current_gif_index + 1}/{len(gif_files)}")
                    elif result == "mode":
                        switch_mode()
                    else:
                        show_please_wait()
                        current_gif_index = (current_gif_index + 1) % len(gif_files)
                        print(f"Error with current GIF, trying next: {current_gif_index + 1}/{len(gif_files)}")
                else:
                    # No GIF files - just handle mode switching
                    pressed, direction = button_pressed()
                    if pressed and direction == "mode":
                        switch_mode()
                    await asyncio.sleep(0.1)
            
            elif current_mode == "clock":
                update_clock_display()
                await asyncio.sleep(1)
                
                pressed, direction = button_pressed()
                if pressed and direction == "mode":
                    switch_mode()
            
            elif current_mode == "led_control":
                pressed, direction = button_pressed()
                if pressed:
                    if direction == "mode":
                        switch_mode()
                    elif direction == "next" or direction == "previous":
                        switch_led_state(direction)
                
                if led_control_state == 4:
                    handle_flashing()
                
                await asyncio.sleep(0.1)
            
            elif current_mode == "mic":
                # Update wave display with new sample
                update_wave_display()
                
                # Check for button presses
                pressed, direction = button_pressed()
                if pressed:
                    if direction == "mode":
                        switch_mode()
                    # Note: next/prev buttons don't do anything in Mic mode
                    # since there's nothing to switch between
                
                # Faster sampling for wave display
                await asyncio.sleep(0.005)  # 5ms delay for ~200Hz sampling

        except Exception as e:
            print(f"Fatal error in main loop: {e}")
            show_error("Fatal error, resetting")
            current_gif_index = 0
            current_mode = "gif"
            stop_audio()
            gc.collect()

# Run the main async loop
if not gif_files:
    show_error("No GIFs found")
    while True:
        time.sleep(1)
else:
    print(f"Found {len(gif_files)} GIFs in /gifs")
    print("Initializing buttons...")
    time.sleep(1)

    asyncio.run(main_async_loop())