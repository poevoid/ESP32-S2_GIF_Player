#include <WiFi.h>
#include <NTPClient.h>
#include <WiFiUdp.h>
#include <SPI.h>
#include <U8g2lib.h>
#include <AnimatedGIF.h>
#include <FS.h>
#include <LittleFS.h>

// ============== WiFi & NTP ==============
#define WIFI_SSID "Polaris"
#define WIFI_PASSWORD "Gaystuffs"
#define TZ_OFFSET -21600

WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, "pool.ntp.org", TZ_OFFSET*3600, 60000);

// ============== Display (U8g2) – SH1106 128x64, rotated 90° ==============
// Pins: SCK=7, MOSI=11, CS=13, DC=5, RST=3
// Rotation U8G2_R1 → 90° → logical canvas is 64x128
U8G2_SH1106_128X64_NONAME_F_4W_SW_SPI display(
  U8G2_R1,      // rotation 90°
  /*sck=*/7,    // clock
  /*mosi=*/11,  // data
  /*cs=*/13,    // chip select
  /*dc=*/5,     // data/command
  /*reset=*/3   // reset
);

// ============== Button Pins ==============
#define BTN_NEXT 8
#define BTN_PREV 6
#define BTN_MODE 2
#define BUTTON_COOLDOWN_MS 500

// ============== LED Pins ==============
#define FLED_PIN 14
#define UVLED_PIN 10
#define PWM_PIN 18

// ============== Microphone ==============
#define MIC_PIN 12
#define WAVE_WIDTH 64
#define WAVE_HEIGHT 128
#define WAVE_BUFFER_SIZE 64
#define AMP_VAL 1600
#define Y_OFFSET 944

// ============== Canvas size (after rotation) ==============
#define SCREEN_WIDTH 64
#define SCREEN_HEIGHT 128
#define BYTES_PER_COL (SCREEN_HEIGHT / 8)  // 16 bytes per column (vertical packing)

// ============== Global Variables ==============
enum Mode { MODE_GIF,
            MODE_CLOCK,
            MODE_LED,
            MODE_MIC };
Mode currentMode = MODE_GIF;

// Button states
bool btnNextPressed, btnPrevPressed, btnModePressed;
unsigned long lastButtonTime = 0;

// ---------- GIF Playback with AnimatedGIF callbacks ----------
AnimatedGIF g_gif;
bool gifIsOpen = false;
String gifFiles[30];
int gifCount = 0;
int currentGifIndex = 0;
unsigned long nextFrameTime = 0;
// --- Global settings ---
float gifSpeedFactor = 0.3;        // 1.0 = original speed
uint8_t luminanceThreshold = 32;  // 0–255
// Frame buffer – 64 x 128 bits = 64 x 16 bytes (vertical packing)
uint8_t frameBuffer[SCREEN_WIDTH][BYTES_PER_COL] = { { 0 } };
uint8_t previousFrameBuffer[SCREEN_WIDTH][BYTES_PER_COL] = { { 0 } };  // for disposal method 3

// Frame disposal tracking
typedef struct {
  int disposal;
  int x, y, width, height;
  uint8_t buffer[SCREEN_WIDTH][BYTES_PER_COL];  // backup for disposal 3
} FrameInfo;

FrameInfo currentFrameInfo = { 0 };
FrameInfo previousFrameInfo = { 0 };
bool frameBackupSaved = false;

// GIF centering offsets
int x_offset = 0, y_offset = 0;

// File callbacks for LittleFS
static File g_currentGifFile;

static void* GIFOpenFile(const char* fname, int32_t* pSize) {
  g_currentGifFile = LittleFS.open(fname, "r");
  if (!g_currentGifFile) return NULL;
  *pSize = g_currentGifFile.size();
  return (void*)&g_currentGifFile;
}

static void GIFCloseFile(void* pHandle) {
  File* f = (File*)pHandle;
  if (f) f->close();
  gifIsOpen = false;
}

static int32_t GIFReadFile(GIFFILE* pFile, uint8_t* pBuf, int32_t iLen) {
  File* f = (File*)pFile->fHandle;
  int32_t iBytesRead = iLen;
  if ((pFile->iSize - pFile->iPos) < iLen)
    iBytesRead = pFile->iSize - pFile->iPos;
  if (iBytesRead <= 0) return 0;
  iBytesRead = f->read(pBuf, iBytesRead);
  pFile->iPos = f->position();
  return iBytesRead;
}

static int32_t GIFSeekFile(GIFFILE* pFile, int32_t iPosition) {
  File* f = (File*)pFile->fHandle;
  f->seek(iPosition, SeekSet);
  pFile->iPos = f->position();
  return pFile->iPos;
}

void GIFDraw(GIFDRAW* pDraw);  // forward declaration
// ------------------------------------------------------------

// LED control
int ledState = 0;
bool flashState = 0;
unsigned long lastFlashTime = 0;
const unsigned long FLASH_INTERVAL_MS = 500;

// Wave display – simple circular buffer, draw directly
uint16_t waveBuffer[WAVE_BUFFER_SIZE];
int waveIndex = 0;

// Clock
unsigned long lastClockUpdate = 0;

// ============== Function Prototypes ==============
void cleanupMacOSFiles(const char* path);
void listGIFFiles();
void switchMode();
void handleGIFMode();
void handleClockMode();
void handleLEDMode();
void handleMicMode();
void updateClockDisplay();
void updateLEDDisplay();
void handleFlashing();
void clearWaveDisplay();
void updateWaveDisplay();
void drawPleaseWait();

// Frame buffer helpers
void setPixel(int x, int y, bool white);
bool getPixel(int x, int y);
void clearFrameBuffer();
void updateDisplayFromBuffer();

// ============== Setup ==============
void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nESP32-S2 Mini U8g2 + LittleFS GIF Player");

  // Mount LittleFS
  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS mount failed");
    return;
  }

  cleanupMacOSFiles("/gifs");
  cleanupMacOSFiles("/wavs");

  // Initialize U8g2 display
  display.begin();
  display.setPowerSave(0);
  display.clearBuffer();
  display.sendBuffer();

  // Initialize AnimatedGIF
  g_gif.begin(GIF_PALETTE_RGB565_LE);

  // Buttons with internal pull‑up
  pinMode(BTN_NEXT, INPUT_PULLUP);
  pinMode(BTN_PREV, INPUT_PULLUP);
  pinMode(BTN_MODE, INPUT_PULLUP);

  // LEDs
  pinMode(FLED_PIN, OUTPUT);
  pinMode(UVLED_PIN, OUTPUT);
  digitalWrite(FLED_PIN, LOW);
  digitalWrite(UVLED_PIN, LOW);
  pinMode(PWM_PIN, OUTPUT);
  analogWrite(PWM_PIN, 0);

  // Microphone
  analogReadResolution(12);

  // WiFi & NTP
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to WiFi");
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi connected");
    timeClient.begin();
    timeClient.update();
    Serial.println("NTP time synchronized");
  } else {
    Serial.println("\nWiFi failed – continuing without time");
  }

  // List GIFs from LittleFS
  listGIFFiles();

  // Initialise wave buffer
  for (int i = 0; i < WAVE_BUFFER_SIZE; i++) waveBuffer[i] = SCREEN_HEIGHT / 2;

  if (gifCount == 0) {
    display.clearBuffer();
    display.setFont(u8g2_font_6x10_tf);
    display.drawStr(0, 20, "No GIFs found");
    display.sendBuffer();
    delay(2000);
  }
}

// ============== Main Loop ==============
void loop() {
  // Cooldown debounce
  if (millis() - lastButtonTime > BUTTON_COOLDOWN_MS) {
    btnNextPressed = (digitalRead(BTN_NEXT) == LOW);
    btnPrevPressed = (digitalRead(BTN_PREV) == LOW);
    btnModePressed = (digitalRead(BTN_MODE) == LOW);
    if (btnNextPressed || btnPrevPressed || btnModePressed) {
      lastButtonTime = millis();
    }
  } else {
    btnNextPressed = btnPrevPressed = btnModePressed = false;
  }

  if (btnModePressed) switchMode();

  switch (currentMode) {
    case MODE_GIF: handleGIFMode(); break;
    case MODE_CLOCK: handleClockMode(); break;
    case MODE_LED: handleLEDMode(); break;
    case MODE_MIC: handleMicMode(); break;
  }

  delay(1);
}

// ============== Mode Switching ==============
void switchMode() {
  // Turn off LEDs and stop audio
  digitalWrite(FLED_PIN, LOW);
  digitalWrite(UVLED_PIN, LOW);
  analogWrite(PWM_PIN, 0);

  // Close any open GIF
  if (currentMode == MODE_GIF && gifIsOpen) {
    g_gif.close();
    gifIsOpen = false;
  }

  switch (currentMode) {
    case MODE_GIF:
      currentMode = MODE_CLOCK;
      updateClockDisplay();
      break;
    case MODE_CLOCK:
      currentMode = MODE_LED;
      updateLEDDisplay();
      break;
    case MODE_LED:
      currentMode = MODE_MIC;
      clearWaveDisplay();
      waveIndex = 0;
      break;
    case MODE_MIC:
      currentMode = MODE_GIF;
      currentGifIndex = 0;
      clearFrameBuffer();
      break;
  }
  Serial.print("Mode switched to ");
  Serial.println(currentMode);
}

// ============== GIF Mode ==============
void handleGIFMode() {
  if (gifCount == 0) {
    delay(100);
    return;
  }

  // Open GIF if not already open
  if (!gifIsOpen) {
    String path = gifFiles[currentGifIndex];
    if (g_gif.open(path.c_str(), GIFOpenFile, GIFCloseFile, GIFReadFile, GIFSeekFile, GIFDraw)) {
      Serial.println("Opened " + path);
      gifIsOpen = true;
      nextFrameTime = 0;

      // Calculate centering offsets
      x_offset = (SCREEN_WIDTH - g_gif.getCanvasWidth()) / 2;
      if (x_offset < 0) x_offset = 0;
      y_offset = (SCREEN_HEIGHT - g_gif.getCanvasHeight()) / 2;
      if (y_offset < 0) y_offset = 0;

      // Clear frame buffer for new GIF
      clearFrameBuffer();
    } else {
      Serial.println("Failed to open " + path);
      currentGifIndex = (currentGifIndex + 1) % gifCount;
      return;
    }
  }

  // Play next frame when due
  if (millis() >= nextFrameTime) {
    int delayMs = 0;
    if (g_gif.playFrame(false, &delayMs, NULL)) {
      // Frame drawn into frameBuffer, now update display
      updateDisplayFromBuffer();
      unsigned long playDelay = (unsigned long)(delayMs * gifSpeedFactor);
      //if (playDelay < 20) playDelay = 20;  // never go below 20 ms
      nextFrameTime = millis() + playDelay;
    } else {
      // GIF finished – restart
      g_gif.close();
      gifIsOpen = false;
    }
  }

  // Button: next/previous GIF
  if (btnNextPressed) {
    drawPleaseWait();
    g_gif.close();
    gifIsOpen = false;
    currentGifIndex = (currentGifIndex + 1) % gifCount;
  }
  if (btnPrevPressed) {
    drawPleaseWait();
    g_gif.close();
    gifIsOpen = false;
    currentGifIndex = (currentGifIndex - 1 + gifCount) % gifCount;
  }
}

// ============== GIF Drawing Callback ==============
void GIFDraw(GIFDRAW *pDraw) {
  uint8_t *s = pDraw->pPixels;
  uint16_t *palette = pDraw->pPalette;
  int y = pDraw->iY + pDraw->y + y_offset;

  // --- START OF FRAME: handle previous frame's disposal ---
  if (pDraw->y == 0) {
    applyDisposal();

    // Store current frame info for NEXT frame
    currentFrameInfo.disposal = pDraw->ucDisposalMethod;
    currentFrameInfo.x = pDraw->iX + x_offset;
    currentFrameInfo.y = pDraw->iY + y_offset;
    currentFrameInfo.width = pDraw->iWidth;
    currentFrameInfo.height = pDraw->iHeight;

    // For disposal method 3 (restore to previous), save current buffer
    if (pDraw->ucDisposalMethod == 3 && !frameBackupSaved) {
      memcpy(currentFrameInfo.buffer, frameBuffer, sizeof(frameBuffer));
      frameBackupSaved = true;
    }
  }

  // --- DRAW CURRENT LINE WITH LUMINANCE THRESHOLD ---
  for (int x = 0; x < pDraw->iWidth; x++) {
    int drawX = pDraw->iX + x + x_offset;
    if (drawX >= SCREEN_WIDTH) break;

    if (y >= 0 && y < SCREEN_HEIGHT && drawX >= 0) {
      // Skip transparent pixels
      if (pDraw->ucHasTransparency && s[x] == pDraw->ucTransparent) continue;

      if (palette) {
        uint16_t color = palette[s[x]];

        // Extract RGB565 components
        uint8_t r5 = (color >> 11) & 0x1F;
        uint8_t g6 = (color >>  5) & 0x3F;
        uint8_t b5 = (color      ) & 0x1F;

        // Scale to 8‑bit per channel
        uint8_t r = (r5 * 255 + 15) / 31;
        uint8_t g = (g6 * 255 + 31) / 63;
        uint8_t b = (b5 * 255 + 15) / 31;

        // Luminance (Rec. 709) – result 0..255
        uint8_t lum = (r * 2126 + g * 7152 + b * 722) >> 13;

        // Apply user‑adjustable threshold
        if (lum > luminanceThreshold) {
          setPixel(drawX, y, true);   // white
        } else {
          setPixel(drawX, y, false);  // black
        }
      }
    }
  }

  // --- END OF FRAME: store previous frame info ---
  if ((pDraw->y + 1) >= pDraw->iHeight) {
    previousFrameInfo = currentFrameInfo;
    frameBackupSaved = false;
  }
}


// Apply disposal method from previous frame
void applyDisposal() {
  switch (previousFrameInfo.disposal) {
    case 0:  // no disposal – keep
    case 1:  // do not dispose – keep
      break;

    case 2:
      {  // restore to background (black)
        for (int fy = previousFrameInfo.y; fy < previousFrameInfo.y + previousFrameInfo.height; fy++) {
          if (fy < 0 || fy >= SCREEN_HEIGHT) continue;
          for (int fx = previousFrameInfo.x; fx < previousFrameInfo.x + previousFrameInfo.width; fx++) {
            if (fx < 0 || fx >= SCREEN_WIDTH) continue;
            setPixel(fx, fy, false);
          }
        }
        break;
      }

    case 3:  // restore to previous
      memcpy(frameBuffer, previousFrameInfo.buffer, sizeof(frameBuffer));
      break;
  }
}

// ============== Frame Buffer Helpers ==============
void setPixel(int x, int y, bool white) {
  if (x < 0 || x >= SCREEN_WIDTH || y < 0 || y >= SCREEN_HEIGHT) return;
  int byteIdx = y / 8;
  int bitIdx = y % 8;
  if (white)
    frameBuffer[x][byteIdx] |= (1 << bitIdx);
  else
    frameBuffer[x][byteIdx] &= ~(1 << bitIdx);
}

bool getPixel(int x, int y) {
  if (x < 0 || x >= SCREEN_WIDTH || y < 0 || y >= SCREEN_HEIGHT) return false;
  int byteIdx = y / 8;
  int bitIdx = y % 8;
  return (frameBuffer[x][byteIdx] >> bitIdx) & 1;
}

void clearFrameBuffer() {
  memset(frameBuffer, 0, sizeof(frameBuffer));
}

void updateDisplayFromBuffer() {
  display.clearBuffer();
  for (int x = 0; x < SCREEN_WIDTH; x++) {
    for (int y = 0; y < SCREEN_HEIGHT; y++) {
      if (getPixel(x, y)) {
        display.drawPixel(x, y);  // U8g2 uses (x,y) with origin top‑left
      }
    }
  }
  display.sendBuffer();
}

// ============== Clock Mode ==============
void handleClockMode() {
  if (millis() - lastClockUpdate >= 1000) {
    updateClockDisplay();
    lastClockUpdate = millis();
  }
}

void updateClockDisplay() {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);

  if (WiFi.status() == WL_CONNECTED) {
    timeClient.update();
    unsigned long epoch = timeClient.getEpochTime();
    struct tm* ptm = localtime((time_t*)&epoch);

    int hour = ptm->tm_hour % 12;
    if (hour == 0) hour = 12;
    char timeStr[20];
    sprintf(timeStr, "%02d:%02d:%02d %s", hour, ptm->tm_min, ptm->tm_sec,
            (ptm->tm_hour < 12) ? "AM" : "PM");

    char dateStr[20];
    sprintf(dateStr, "%04d-%02d-%02d", ptm->tm_year + 1900, ptm->tm_mon + 1, ptm->tm_mday);

    display.drawStr(5, 45, timeStr);
    //display.drawStr(5, 40, dateStr);
  } else {
    display.drawStr(10, 30, "No WiFi");
  }
  display.sendBuffer();
}

// ============== LED Control Mode ==============
void handleLEDMode() {
  if (btnNextPressed) {
    ledState = (ledState + 1) % 5;
    updateLEDDisplay();
  }
  if (btnPrevPressed) {
    ledState = (ledState - 1 + 5) % 5;
    updateLEDDisplay();
  }
  if (ledState == 4) handleFlashing();
  delay(50);
}

void updateLEDDisplay() {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(5, 10, "LED GUI");

  switch (ledState) {
    case 0:
      display.drawStr(5, 30, "LED1: ON ");
      display.drawStr(5, 45, "LED2: OFF");
      digitalWrite(FLED_PIN, HIGH);
      digitalWrite(UVLED_PIN, LOW);
      flashState = 0;
      break;
    case 1:
      display.drawStr(5, 30, "LED1: OFF");
      display.drawStr(5, 45, "LED2: ON ");
      digitalWrite(FLED_PIN, LOW);
      digitalWrite(UVLED_PIN, HIGH);
      flashState = 0;
      break;
    case 2:
      display.drawStr(5, 30, "LED1: ON ");
      display.drawStr(5, 45, "LED2: ON ");
      digitalWrite(FLED_PIN, HIGH);
      digitalWrite(UVLED_PIN, HIGH);
      flashState = 0;
      break;
    case 3:
      display.drawStr(5, 30, "LED1: OFF");
      display.drawStr(5, 45, "LED2: OFF");
      digitalWrite(FLED_PIN, LOW);
      digitalWrite(UVLED_PIN, LOW);
      flashState = 0;
      break;
    case 4:
      display.drawStr(5, 30, "LED1: FLASHING");
      display.drawStr(5, 45, "LED2: FLASHING");
      lastFlashTime = millis();
      break;
  }
  display.sendBuffer();
}

void handleFlashing() {
  if (millis() - lastFlashTime >= FLASH_INTERVAL_MS) {
    if (flashState == 0) {
      digitalWrite(FLED_PIN, HIGH);
      digitalWrite(UVLED_PIN, LOW);
      flashState = 1;
    } else {
      digitalWrite(FLED_PIN, LOW);
      digitalWrite(UVLED_PIN, HIGH);
      flashState = 0;
    }
    lastFlashTime = millis();
  }
}

// ============== Mic Mode ==============
void handleMicMode() {
  updateWaveDisplay();
}

void clearWaveDisplay() {
  // nothing to clear – redrawn every frame
}

void updateWaveDisplay() {
  uint16_t sample = analogRead(MIC_PIN);

  // Map to screen height (0–127)
  int newY = ((sample * AMP_VAL) / 64) - (Y_OFFSET / 512);
  newY = (newY / 512) + 64;
  newY = constrain(newY, 0, SCREEN_HEIGHT - 1);

  waveBuffer[waveIndex] = newY;

  // Draw the waveform directly on U8g2 buffer
  display.clearBuffer();

  // Plot all points in buffer and connect with lines
  for (int i = 0; i < WAVE_BUFFER_SIZE - 1; i++) {
    int x1 = i;
    int x2 = i + 1;
    int y1 = waveBuffer[(waveIndex + i) % WAVE_BUFFER_SIZE];
    int y2 = waveBuffer[(waveIndex + i + 1) % WAVE_BUFFER_SIZE];
    display.drawLine(x1, y1, x2, y2);
  }

  display.sendBuffer();

  // Update index for next sample
  waveIndex = (waveIndex + 1) % WAVE_BUFFER_SIZE;
}

// ============== Helper Functions ==============
void cleanupMacOSFiles(const char* path) {
  File dir = LittleFS.open(path);
  if (!dir || !dir.isDirectory()) return;

  File file;
  int removed = 0;
  while (file = dir.openNextFile()) {
    String fname = file.name();
    if (fname.startsWith("._")) {
      LittleFS.remove(String(path) + "/" + fname);
      Serial.println("Removed macOS file: " + String(path) + "/" + fname);
      removed++;
    }
    file.close();
  }
  dir.close();
  if (removed > 0) {
    Serial.printf("Cleaned %d files from %s\n", removed, path);
  }
}

void listGIFFiles() {
  gifCount = 0;
  File dir = LittleFS.open("/gifs");
  if (!dir || !dir.isDirectory()) {
    LittleFS.mkdir("/gifs");
    return;
  }

  File file;
  while (file = dir.openNextFile()) {
    String fname = file.name();
    if (!file.isDirectory() && fname.endsWith(".gif") && !fname.startsWith("._")) {
      if (gifCount < 30) {
        gifFiles[gifCount] = "/gifs/" + fname;
        gifCount++;
        Serial.println("Found GIF: " + gifFiles[gifCount - 1]);
      }
    }
    file.close();
  }
  dir.close();
}

void drawPleaseWait() {
  display.clearBuffer();
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(10, 50, "Just a");
  display.drawStr(10, 70, "sec...");
  display.sendBuffer();
  delay(500);
}