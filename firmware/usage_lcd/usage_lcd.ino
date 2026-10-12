// Claude usage LCD: shows the five_hour and seven_day usage windows on a 16x2 I2C LCD.
// The protocol and the screen are in docs/PROTOCOL.md; all of that logic is in UsageDisplay,
// this file only connects it to Serial and the LCD.
// Library: "LiquidCrystal I2C" by Frank de Brabander (Arduino IDE Library Manager).
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <new.h>
#include <avr/pgmspace.h>
#include "UsageDisplay.h"

// Bar cells 0-5: that many pixel columns filled inside a tube outline, so empty cells still show.
const uint8_t kBarGlyphs[6][8] PROGMEM = {
  {0x00, 0x1F, 0x00, 0x00, 0x00, 0x00, 0x1F, 0x00},
  {0x00, 0x1F, 0x10, 0x10, 0x10, 0x10, 0x1F, 0x00},
  {0x00, 0x1F, 0x18, 0x18, 0x18, 0x18, 0x1F, 0x00},
  {0x00, 0x1F, 0x1C, 0x1C, 0x1C, 0x1C, 0x1F, 0x00},
  {0x00, 0x1F, 0x1E, 0x1E, 0x1E, 0x1E, 0x1F, 0x00},
  {0x00, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x00},
};

static void sendLine(const char* line) {
  Serial.print(line);
  Serial.write('\n');
}

static UsageDisplay usage(sendLine);
static LiquidCrystal_I2C* lcd;                     // set once an LCD answers
static uint8_t lcdMem[sizeof(LiquidCrystal_I2C)];  // the LCD object lives here, no heap
static uint8_t shown[2][16];                       // what the LCD shows now
static uint32_t lastProbeMs;

static bool answers(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

// 0x27 and 0x3F first, then the rest of the PCF8574 (0x20-0x27) and PCF8574A (0x38-0x3F) ranges.
static uint8_t findLcd() {
  if (answers(0x27)) return 0x27;
  if (answers(0x3F)) return 0x3F;
  for (uint8_t a = 0x20; a < 0x40; ++a) {
    if (a == 0x28) a = 0x38;
    if (a != 0x27 && a != 0x3F && answers(a)) return a;
  }
  return 0;
}

static bool startLcd() {
  uint8_t addr = findLcd();
  if (!addr) return false;
  lcd = new (lcdMem) LiquidCrystal_I2C(addr, 16, 2);
  lcd->init();  // also clears the screen
  lcd->backlight();
  uint8_t glyph[8];
  for (uint8_t i = 0; i < 6; ++i) {
    memcpy_P(glyph, kBarGlyphs[i], sizeof glyph);
    lcd->createChar(i, glyph);
  }
  memset(shown, ' ', sizeof shown);
  digitalWrite(LED_BUILTIN, LOW);
  usage.hello();
  return true;
}

// Writes only the cells that differ from what the LCD shows.
static void draw() {
  for (uint8_t r = 0; r < 2; ++r) {
    const uint8_t* row = usage.row(r);
    bool atCell = false;  // LCD cursor already on this cell?
    for (uint8_t c = 0; c < 16; ++c) {
      if (row[c] == shown[r][c]) {
        atCell = false;
        continue;
      }
      if (!atCell) lcd->setCursor(c, r);
      lcd->write(row[c]);
      shown[r][c] = row[c];
      atCell = true;
    }
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(LED_BUILTIN, OUTPUT);
  Wire.begin();
  Wire.setWireTimeout(25000, true);  // a miswired bus must not hang the sketch
  if (!startLcd()) {
    Serial.print(F("E|no-lcd\n"));
    lastProbeMs = millis();
  }
}

void loop() {
  while (Serial.available() > 0) usage.feed((uint8_t)Serial.read(), millis());
  usage.tick(millis());
  if (lcd) {
    draw();
    return;
  }
  // No LCD: blink fast, look again and report every 5 s.
  digitalWrite(LED_BUILTIN, (millis() >> 7) & 1);
  if (millis() - lastProbeMs >= 5000) {
    lastProbeMs = millis();
    if (!startLcd()) Serial.print(F("E|no-lcd\n"));
  }
}
