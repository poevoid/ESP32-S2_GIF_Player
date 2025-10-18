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

# Release any existing displays
displayio.release_displays()

fled = digitalio.DigitalInOut(board.IO4)
fled.direction = digitalio.Direction.OUTPUT

uvled = digitalio.DigitalInOut(board.IO6)
uvled.direction = digitalio.Direction.OUTPUT

fled.value = False
uvled.value = False

# Initialize PWM once and keep it alive
pwm = pwmio.PWMOut(board.IO18, frequency=40000, duty_cycle=0)
DELAY_COUNT = 65  # Optimal delay for Lolin S2 Mini

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
    next_button = PhysicalButton(board.IO9)
    prev_button = PhysicalButton(board.IO11)
    mode_button = PhysicalButton(board.IO2)  # New mode button
except Exception as e:
    print(f"Button init error: {e}")

# Button press cooldown to prevent spam (in seconds)
BUTTON_COOLDOWN = 0.5
last_button_press = 0

# Mode state
current_mode = "gif"  # Start in GIF mode

# LED Control Mode State
# 0: FLED on, UVLED off
# 1: UVLED on, FLED off  
# 2: Both on
# 3: Both off
# 4: Flashing between FLED and UVLED
led_control_state = 0
flash_state = 0
last_flash_time = 0
FLASH_INTERVAL = 0.5  # seconds between flash changes

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

def play_wav_simple(filename):
    """Play WAV file using simple PWM approach - EXACT copy of original"""
    try:
        print(f"Playing: {filename}")
        
        with open(filename, "rb") as f:
            # Skip WAV header (standard 44 bytes)
            f.seek(44)
            
            # Play all samples
            while True:
                chunk = f.read(512)  # Read in chunks
                if not chunk:
                    break
                    
                for sample in chunk:
                    # Convert 8-bit sample to 16-bit PWM duty cycle
                    pwm.duty_cycle = sample * 257
                    
                    # Delay loop - this value works well for Lolin S2 Mini
                    for _ in range(DELAY_COUNT):
                        pass
        
        print("Playback complete")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        stop_audio()

def play_wav_with_gif(wav_filename, gif_path):
    """Play WAV file while simultaneously displaying GIF - optimized for both"""
    odg = None
    current_gif = None
    
    try:
        print(f"Playing: {wav_filename} with GIF: {gif_path}")
        
        # Load and display the GIF first
        try:
            odg = gifio.OnDiskGif(gif_path)
            current_gif = displayio.TileGrid(
                odg.bitmap,
                pixel_shader=displayio.ColorConverter(
                    input_colorspace=displayio.Colorspace.L8
                ),
                x=(128 - odg.bitmap.width) // 2 if odg.bitmap.width < 128 else 0,
                y=(64 - odg.bitmap.height) // 2 if odg.bitmap.height < 64 else 0
            )
            
            # Clear and set up display for GIF
            while len(main_group) > 0:
                main_group.pop()
            main_group.append(current_gif)
            
            next_frame_time = time.monotonic()
            frame_delay = odg.next_frame()  # Get first frame delay
            
        except Exception as e:
            print(f"GIF error: {e}")
            # Show error on display
            while len(main_group) > 0:
                main_group.pop()
            error_text = label.Label(terminalio.FONT, text="GIF Error", color=0xFFFFFF)
            error_text.x = 10
            error_text.y = HEIGHT // 2
            main_group.append(error_text)
            odg = None
        
        # Now play WAV with the exact original timing while updating GIF
        with open(wav_filename, "rb") as f:
            # Skip WAV header (standard 44 bytes)
            f.seek(44)
            
            # Use original 512-byte chunks for perfect audio timing
            chunk_size = 512
            
            # Counter for more frequent GIF updates
            sample_counter = 0
            
            # Play all samples
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                
                # Process audio samples with perfect timing
                for sample in chunk:
                    # Convert 8-bit sample to 16-bit PWM duty cycle
                    pwm.duty_cycle = sample * 257
                    
                    # EXACT original delay for perfect audio
                    for _ in range(DELAY_COUNT):
                        pass
                    
                    # Update GIF frame more frequently - check every 16 samples
                    sample_counter += 1
                    if sample_counter >= 16:  # Update every 16 samples
                        if odg and time.monotonic() >= next_frame_time:
                            try:
                                frame_delay = odg.next_frame()
                                next_frame_time = time.monotonic() + frame_delay
                            except Exception as e:
                                print(f"GIF frame error: {e}")
                                odg = None
                        sample_counter = 0
                
                # Check for button presses occasionally (less frequent)
                pressed, direction = button_pressed()
                if pressed:
                    return direction
        
        print("Playback complete")
        return "complete"
        
    except Exception as e:
        print(f"Error in WAV+GIF: {e}")
        return "error"
    finally:
        # Clean up GIF resources
        if odg:
            try:
                odg.deinit()
            except:
                pass
        
        # Clear the display
        try:
            while len(main_group) > 0:
                main_group.pop()
        except:
            pass
        
        # Stop audio
        stop_audio()
        
        # Force garbage collection to prevent memory fragmentation
        gc.collect()

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

fled_label = label.Label(terminalio.FONT, text="FLED: OFF", color=0xFFFFFF)
fled_label.x = 10
fled_label.y = 30

uvled_label = label.Label(terminalio.FONT, text="UVLED: OFF", color=0xFFFFFF)
uvled_label.x = 10
uvled_label.y = 50

led_control_group = displayio.Group()
led_control_group.append(led_title)
led_control_group.append(fled_label)
led_control_group.append(uvled_label)

# Create WAV player display elements
wav_title = label.Label(terminalio.FONT, text="WAV PLAYER", color=0xFFFFFF)
wav_title.x = 10
wav_title.y = 10

wav_file_label = label.Label(terminalio.FONT, text="No WAV files", color=0xFFFFFF)
wav_file_label.x = 10
wav_file_label.y = 30

wav_status_label = label.Label(terminalio.FONT, text="Stopped", color=0xFFFFFF)
wav_status_label.x = 10
wav_status_label.y = 50

wav_group = displayio.Group()
wav_group.append(wav_title)
wav_group.append(wav_file_label)
wav_group.append(wav_status_label)

def update_wav_display():
    """Update the WAV player display"""
    if wav_files:
        filename = wav_files[current_wav_index]
        wav_file_label.text = filename
        wav_status_label.text = "Playing"
    else:
        wav_file_label.text = "No WAV files"
        wav_status_label.text = "Stopped"

def update_led_display():
    """Update the LED control display based on current state"""
    global flash_state, last_flash_time
    
    if led_control_state == 0:  # FLED on, UVLED off
        fled_label.text = "FLED: ON"
        uvled_label.text = "UVLED: OFF"
        fled.value = True
        uvled.value = False
        flash_state = 0
    elif led_control_state == 1:  # UVLED on, FLED off
        fled_label.text = "FLED: OFF" 
        uvled_label.text = "UVLED: ON"
        fled.value = False
        uvled.value = True
        flash_state = 0
    elif led_control_state == 2:  # Both on
        fled_label.text = "FLED: ON"
        uvled_label.text = "UVLED: ON"
        fled.value = True
        uvled.value = True
        flash_state = 0
    elif led_control_state == 3:  # Both off
        fled_label.text = "FLED: OFF"
        uvled_label.text = "UVLED: OFF"
        fled.value = False
        uvled.value = False
        flash_state = 0
    elif led_control_state == 4:  # Flashing mode
        fled_label.text = "FLED: FLASHING"
        uvled_label.text = "UVLED: FLASHING"
        # Flash state will be handled in the main loop
        last_flash_time = time.monotonic()

def handle_flashing():
    """Handle the flashing mode - called repeatedly in main loop"""
    global flash_state, last_flash_time
    
    current_time = time.monotonic()
    if current_time - last_flash_time >= FLASH_INTERVAL:
        if flash_state == 0:
            # FLED on, UVLED off
            fled.value = True
            uvled.value = False
            flash_state = 1
        else:
            # UVLED on, FLED off
            fled.value = False
            uvled.value = True
            flash_state = 0
        last_flash_time = current_time

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
    """Switch between GIF, clock, LED control, WAV, and WAV+GIF modes"""
    global current_mode
    if current_mode == "gif":
        current_mode = "clock"
        display.root_group = clock_group
        turn_off_all_leds()  # Turn off LEDs when leaving LED mode
        stop_audio()  # Stop audio when leaving WAV mode
        update_clock_display()  # Update immediately when switching to clock
        print("Switched to Clock mode")
    elif current_mode == "clock":
        current_mode = "led_control"
        display.root_group = led_control_group
        stop_audio()  # Stop audio when leaving WAV mode
        update_led_display()  # Update immediately when switching to LED control
        print("Switched to LED Control mode")
    elif current_mode == "led_control":
        current_mode = "wav"
        display.root_group = wav_group
        turn_off_all_leds()  # Turn off LEDs when leaving LED mode
        stop_audio()  # Ensure audio is stopped when entering WAV mode
        update_wav_display()  # Update immediately when switching to WAV mode
        print("Switched to WAV mode")
    elif current_mode == "wav":
        current_mode = "wav_gif"
        # For WAV+GIF mode, we use the main_group to display the actual GIF
        display.root_group = main_group
        turn_off_all_leds()  # Turn off LEDs when leaving LED mode
        stop_audio()  # Ensure audio is stopped when entering WAV+GIF mode
        print("Switched to WAV+GIF mode")
    else:
        current_mode = "gif"
        display.root_group = main_group
        turn_off_all_leds()  # Turn off LEDs when leaving LED mode
        stop_audio()  # Stop audio when leaving WAV mode
        print("Switched to GIF mode")

def switch_led_state(direction):
    """Switch between LED states in LED control mode"""
    global led_control_state
    if direction == "next":
        led_control_state = (led_control_state + 1) % 5  # Now 5 states
    elif direction == "previous":
        led_control_state = (led_control_state - 1) % 5
    
    update_led_display()
    print(f"LED State: {led_control_state}")

def switch_wav_file(direction):
    """Switch between WAV files"""
    global current_wav_index
    stop_audio()  # Stop current playback
    
    if direction == "next":
        current_wav_index = (current_wav_index + 1) % len(wav_files)
    elif direction == "previous":
        current_wav_index = (current_wav_index - 1) % len(wav_files)
    
    update_wav_display()
    print(f"WAV File: {wav_files[current_wav_index]}")

def switch_wav_gif_pair(direction):
    """Switch between WAV+GIF pairs"""
    global current_pair_index
    stop_audio()  # Stop current playback
    
    if direction == "next":
        current_pair_index = (current_pair_index + 1) % len(wav_gif_pairs)
    elif direction == "previous":
        current_pair_index = (current_pair_index - 1) % len(wav_gif_pairs)
    
    current_pair = wav_gif_pairs[current_pair_index]
    print(f"WAV+GIF: {current_pair['wav']} + {current_pair['gif']}")

def show_please_wait():
    """Show a simple 'please wait' message"""
    while len(main_group) > 0:
        main_group.pop()

    text = "Please wait..."
    text_area = label.Label(terminalio.FONT, text=text, color=0xFFFFFF)
    text_area.x = 10
    text_area.y = HEIGHT // 2

    main_group.append(text_area)
    time.sleep(0.5)  # Reduced from 2.0 to 0.5 seconds for faster switching

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
    """Get all GIF files from /gifs directory"""
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

def get_wav_files():
    """Get all WAV files from /wavs directory"""
    wav_dir = "/wavs"
    files = []

    try:
        os.mkdir(wav_dir)
    except OSError:
        pass

    for file in os.listdir(wav_dir):
        if file.lower().endswith('.wav'):
            files.append(f"{wav_dir}/{file}")

    return sorted(files)

def get_wav_gif_pairs():
    """Find pairs of GIFs and WAVs with the same base name"""
    pairs = []
    
    gif_files = get_gif_files()
    wav_files = get_wav_files()
    
    # Create lookup dictionaries for faster matching
    gif_dict = {}
    for gif_path in gif_files:
        # Extract base name without extension using string methods
        filename = gif_path.split('/')[-1]  # Get the filename part after last '/'
        base_name = filename.lower().replace('.gif', '')
        gif_dict[base_name] = gif_path
    
    wav_dict = {}
    for wav_path in wav_files:
        # Extract base name without extension using string methods
        filename = wav_path.split('/')[-1]  # Get the filename part after last '/'
        base_name = filename.lower().replace('.wav', '')
        wav_dict[base_name] = wav_path
    
    # Find matching pairs
    for base_name in gif_dict:
        if base_name in wav_dict:
            pairs.append({
                'gif': gif_dict[base_name],
                'wav': wav_dict[base_name],
                'name': base_name
            })
    
    return sorted(pairs, key=lambda x: x['name'])

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

# Initialize files
gif_files = get_gif_files()
wav_files = get_wav_files()  # Now gets from /wavs
wav_gif_pairs = get_wav_gif_pairs()

current_gif_index = 0
current_wav_index = 0
current_pair_index = 0

if not wav_files:
    print("No WAV files found in /wavs! Please add .wav files to /wavs directory.")
else:
    print(f"Found {len(wav_files)} WAV files in /wavs")

if not wav_gif_pairs:
    print("No WAV+GIF pairs found! Please add matching .gif and .wav files to /gifs and /wavs directories.")
else:
    print(f"Found {len(wav_gif_pairs)} WAV+GIF pairs")

# Main loop
if not gif_files and not wav_files:
    show_error("No GIFs or WAVs found")
    while True:
        time.sleep(1)
else:
    if gif_files:
        print(f"Found {len(gif_files)} GIFs in /gifs")
    if wav_files:
        print(f"Found {len(wav_files)} WAVs in /wavs")

    # Initial setup period for buttons
    print("Initializing buttons...")
    time.sleep(1)

    # Counter to track consecutive WAV+GIF playbacks for memory management
    playback_counter = 0
    
    while True:
        try:
            if current_mode == "gif":
                if gif_files:
                    print(f"Playing GIF {current_gif_index + 1}/{len(gif_files)}")

                    result = play_gif(gif_files[current_gif_index])

                    if result == "next":
                        show_please_wait()  # Replaced interstitial with please wait
                        current_gif_index = (current_gif_index + 1) % len(gif_files)
                        print(f"Switching to next GIF: {current_gif_index + 1}/{len(gif_files)}")
                    elif result == "previous":
                        show_please_wait()  # Replaced interstitial with please wait
                        current_gif_index = (current_gif_index - 1) % len(gif_files)
                        print(f"Switching to previous GIF: {current_gif_index + 1}/{len(gif_files)}")
                    elif result == "mode":
                        switch_mode()
                    else:
                        show_please_wait()  # Replaced interstitial with please wait
                        current_gif_index = (current_gif_index + 1) % len(gif_files)
                        print(f"Error with current GIF, trying next: {current_gif_index + 1}/{len(gif_files)}")
                else:
                    # No GIF files - just handle mode switching
                    pressed, direction = button_pressed()
                    if pressed and direction == "mode":
                        switch_mode()
                    time.sleep(0.1)
            
            elif current_mode == "clock":
                # Clock mode - update time every second without flickering
                update_clock_display()
                time.sleep(1)
                
                # Check for button presses in clock mode
                pressed, direction = button_pressed()
                if pressed:
                    if direction == "mode":
                        switch_mode()
            
            elif current_mode == "led_control":
                # LED Control mode - handle button presses and flashing
                pressed, direction = button_pressed()
                if pressed:
                    if direction == "mode":
                        switch_mode()
                    elif direction == "next" or direction == "previous":
                        switch_led_state(direction)
                
                # Handle flashing mode if active
                if led_control_state == 4:
                    handle_flashing()
                
                time.sleep(0.1)  # Small delay to prevent excessive CPU usage
            
            elif current_mode == "wav":
                # WAV Player mode - use EXACT original playback code
                if wav_files:
                    # Update display to show current file
                    update_wav_display()
                    
                    # Play current WAV file using exact original timing
                    play_wav_simple(wav_files[current_wav_index])
                    
                    # After playback completes, check for button presses
                    start_time = time.monotonic()
                    while time.monotonic() - start_time < 1.0:  # 1 second pause
                        pressed, direction = button_pressed()
                        if pressed:
                            if direction == "mode":
                                switch_mode()
                                break
                            elif direction == "next":
                                switch_wav_file("next")
                                break
                            elif direction == "previous":
                                switch_wav_file("previous")
                                break
                        time.sleep(0.01)
                    
                    # If no button pressed during pause, move to next file
                    else:
                        current_wav_index = (current_wav_index + 1) % len(wav_files)
                else:
                    # No WAV files - just handle mode switching
                    pressed, direction = button_pressed()
                    if pressed and direction == "mode":
                        switch_mode()
                    time.sleep(0.1)
            
            elif current_mode == "wav_gif":
                # WAV+GIF mode - only play pairs that have matching names
                if wav_gif_pairs:
                    current_pair = wav_gif_pairs[current_pair_index]
                    print(f"Starting WAV+GIF: {current_pair['wav']} + {current_pair['gif']}")
                    
                    # Play WAV with GIF using optimized non-blocking approach
                    # This function will display the actual GIF in main_group
                    result = play_wav_with_gif(current_pair['wav'], current_pair['gif'])
                    
                    # Handle result - NO AUTO-ADVANCE, only change on button press
                    if result == "mode":
                        switch_mode()
                    elif result == "next":
                        switch_wav_gif_pair("next")
                    elif result == "previous":
                        switch_wav_gif_pair("previous")
                    # If playback completes without button press, just stay on current pair
                    # and wait for next manual button press
                    
                    # Increment playback counter and force GC every few playbacks
                    playback_counter += 1
                    if playback_counter >= 3:
                        print("Forcing garbage collection...")
                        gc.collect()
                        playback_counter = 0
                    
                    # Small delay between playbacks to prevent rapid cycling
                    time.sleep(0.5)
                else:
                    # No pairs - show error and allow mode switching
                    while len(main_group) > 0:
                        main_group.pop()
                    error_text = label.Label(terminalio.FONT, text="No WAV+GIF pairs", color=0xFFFFFF)
                    error_text.x = 10
                    error_text.y = HEIGHT // 2
                    main_group.append(error_text)
                    
                    pressed, direction = button_pressed()
                    if pressed:
                        if direction == "mode":
                            switch_mode()
                        elif direction == "next" or direction == "previous":
                            # Refresh pairs and try again
                            wav_gif_pairs = get_wav_gif_pairs()
                            if wav_gif_pairs:
                                current_pair_index = 0
                    time.sleep(0.1)

        except Exception as e:
            print(f"Fatal error in main loop: {e}")
            show_error("Fatal error, resetting")
            current_gif_index = 0
            current_wav_index = 0
            current_pair_index = 0
            playback_counter = 0
            stop_audio()  # Ensure audio is stopped on error
            # Force a full garbage collection on error
            gc.collect()
