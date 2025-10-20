## Requirements: 
Circuitpython 9.x on a lolin s2 mini

## Modes

### GIF Mode
- Displays animated GIFs from the `/gifs` directory
- Automatically loops through all available GIFs
- Use next/previous buttons to navigate between GIFs
- Press mode button to switch to clock mode

### Clock Mode  
- Shows current time and date
- Time is automatically synchronized via WiFi (if configured)
- Press mode button to switch to LED control mode

### LED Control Mode
- Controls the built-in FLED (white) and UVLED (purple) lights
- Five different states:
  - FLED on, UVLED off
  - UVLED on, FLED off
  - Both LEDs on
  - Both LEDs off  
  - Flashing alternation between FLED and UVLED
- Use next/previous buttons to cycle through LED states
- Press mode button to switch to WAV mode

### WAV Mode
- Plays audio files from the `/wavs` directory
- Automatically advances to next WAV file after each playback
- Use next/previous buttons to skip between files
- Press mode button to switch to WAV+GIF mode

### WAV+GIF Mode
- **Special feature:** Plays GIF animations with matching audio!
- Pairs files with the same base name from `/gifs` and `/wavs` directories
- Both audio and animation loop continuously until you press a button
- Example: `animation.gif` and `animation.wav` will play together
- Tip: Keep file names short, I try to aim for less than 6 characters but it's not a hard rule.
- Press mode button to return to GIF mode

## WiFi and Time Setup

To enable automatic time synchronization in clock mode, edit the `settings.toml` file in the root directory with a text editor and enter your wifi SSID and password accordingly:

```
CIRCUITPY_WIFI_SSID = "your_wifi_name"
CIRCUITPY_WIFI_PASSWORD = "your_wifi_password"
CIRCUITPY_TZ_OFFSET = "-5"  # Your timezone offset from UTC
```

The timezone offset is in hours (e.g., -5 for EST, -8 for PST, +1 for CET).

## Adding Your Own Content

### GIF Files
- Place `.gif` files in the `/gifs` directory
- Optimal size: 128x64 pixels (will be centered if smaller)
- Must be monochrome(only black and white) or grayscale, if grayscale, the device will try to automatically convert the colorspace to monochrome and display that.

### WAV Files  
- Place `.wav` files in the `/wavs` directory
- Format: 8-bit, mono WAV files
- Sample rate: 8kHz recommended

### Synchronized Pairs
For WAV+GIF mode, use matching base names:
- `myanimation.gif` + `myanimation.wav`
- `effect.gif` + `effect.wav`

The device will automatically detect and pair these files.

## Technical Notes

### Audio + GIF Trade-off
In WAV+GIF mode, both audio playback and GIF animation run simultaneously using cooperative multitasking. This means:

- Both audio and animations will play slower than in their individual modes
- The device balances CPU time between maintaining audio quality and smooth animation using cooperative multitasking.
- This is a deliberate design choice to enable somewhat decent playback on limited hardware. This is truly pushing the device to the limit of what it can do. 

### Button Functions Summary
- **Mode Button**: Cycles through all available modes
- **Next Button**: Advances to next item in current mode
- **Previous Button**: Returns to previous item in current mode

### File Management
The device automatically cleans up macOS metadata files (those starting with `._`) so if you drag and drop gifs onto the device from a Mac, you don't have to worry about anything. "Just works" kinda thing.

## Troubleshooting

- **No content appears**: Please wait and allow the device a good 10-20 seconds to boot up. If still seeing nothing, ensure files are in the correct directories (`/gifs` and `/wavs`)
- **Time doesn't sync**: Check WiFi credentials in `settings.toml`, probably a typo. If not, try a 2.4ghz network instead of 5G, sometimes helps. 
- **Audio glitches**: This is normal in WAV+GIF mode due to the performance trade-off
- **Device unresponsive**: Disconnect and reconnect USB cable to reset
- **Buttons not working**: some of the modes have weird timing for handling button presses, so try holding the button down, or spamming it to get it to work. I know it's a little janky but this is a lot the device is juggling, so it's the best it can do.
