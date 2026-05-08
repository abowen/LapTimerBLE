// LapTimerBLE transponder firmware — Seeed XIAO ESP32-C3.
//
// Advertises BLE local name "LapTimer-<CAR_NUMBER>" at the per-car interval
// from plan.md. CAR_NUMBER is supplied by PlatformIO build flags so each
// flashed binary is bound to a specific car.

#include <Arduino.h>
#include <NimBLEDevice.h>

#ifndef CAR_NUMBER
#error "CAR_NUMBER must be set via build flags (e.g. -DCAR_NUMBER=1). See platformio.ini."
#endif

#if (CAR_NUMBER < 1) || (CAR_NUMBER > 8)
#error "CAR_NUMBER must be between 1 and 8."
#endif

namespace {

// Per-car advertising intervals in milliseconds. Indexed by CAR_NUMBER - 1.
// Must match the table in plan.md.
constexpr uint16_t kAdvIntervalMs[8] = {20, 23, 29, 31, 37, 41, 43, 47};

// NimBLE advertising intervals are expressed in 0.625 ms units.
constexpr uint16_t kAdvIntervalUnits =
    static_cast<uint16_t>((static_cast<uint32_t>(kAdvIntervalMs[CAR_NUMBER - 1]) * 1000UL) / 625UL);

// XIAO ESP32-C3 user LED: GPIO 8, active-low.
constexpr uint8_t kLedPin = 8;

}  // namespace

void setup() {
  Serial.begin(115200);

  pinMode(kLedPin, OUTPUT);
  digitalWrite(kLedPin, HIGH);  // off (active-low)

  char name[16];
  snprintf(name, sizeof(name), "LapTimer-%d", CAR_NUMBER);

  Serial.printf("LapTimerBLE car %d: advertising as '%s' every %u ms\n",
                CAR_NUMBER, name, kAdvIntervalMs[CAR_NUMBER - 1]);

  NimBLEDevice::init(name);
  // +9 dBm: highest level supported uniformly across ESP32 variants.
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  // Pin both bounds so the stack does not randomise within a window.
  adv->setMinInterval(kAdvIntervalUnits);
  adv->setMaxInterval(kAdvIntervalUnits);
  adv->start();
}

void loop() {
  // Heartbeat: 80 ms blink every 2 s — confirms the firmware is alive
  // without distracting the driver.
  static uint32_t last_blink_ms = 0;
  const uint32_t now = millis();
  if (now - last_blink_ms >= 2000) {
    digitalWrite(kLedPin, LOW);   // on
    delay(80);
    digitalWrite(kLedPin, HIGH);  // off
    last_blink_ms = now;
  }
  delay(20);
}
