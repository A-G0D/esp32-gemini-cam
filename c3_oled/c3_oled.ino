// c3_oled.ino - ESP32-C3 SuperMini + 0.49" SSD1306 (64x32) HUD node.
//
// Role: join the PC hotspot and display text the PC pushes to it. The PC runs
// the Gemini result through and POSTs the display text here.
//
// Endpoints:
//   POST /msg   body = the text to show (newlines split lines). Returns "ok".
//   GET  /      status page
//
// Wiring (SSD1306 I2C -> C3 SuperMini):
//   VCC -> 3V3, GND -> GND, SDA -> GPIO8, SCL -> GPIO9   (C3 SuperMini defaults)
//
// Libraries (Arduino Library Manager): "Adafruit SSD1306", "Adafruit GFX".

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ---------------------------------------------------------------- config
// WIFI_SSID / WIFI_PASS come from wifi_config.h (gitignored). Copy
// wifi_config.example.h -> wifi_config.h in this folder and fill it in.
#include "wifi_config.h"
const char* HOSTNAME  = "c3hud";               // reachable as c3hud.local

#define I2C_SDA 8
#define I2C_SCL 9
#define SCREEN_W 64
#define SCREEN_H 32
#define OLED_ADDR 0x3C                          // most 0.49" SSD1306 are 0x3C

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);
WebServer server(80);

String lastMsg = "waiting...";

void render(const String& msg) {
  display.clearDisplay();
  display.setTextSize(1);                       // 6x8 px -> ~10 chars x 4 lines on 64x32
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  int line = 0, start = 0;
  for (int i = 0; i <= msg.length() && line < 4; i++) {
    if (i == msg.length() || msg[i] == '\n') {
      String ln = msg.substring(start, i);
      if (ln.length() > 10) ln = ln.substring(0, 10);   // hard clip to width
      display.setCursor(0, line * 8);
      display.print(ln);
      line++; start = i + 1;
    }
  }
  display.display();
}

void handleMsg() {
  if (server.hasArg("plain")) {
    lastMsg = server.arg("plain");
    render(lastMsg);
    server.send(200, "text/plain", "ok");
  } else {
    server.send(400, "text/plain", "no body");
  }
}

void handleRoot() {
  server.send(200, "text/plain", String("C3 HUD up. POST text to /msg. last: ") + lastMsg);
}

void setup() {
  Serial.begin(115200);
  Wire.begin(I2C_SDA, I2C_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("SSD1306 not found (check wiring / address 0x3C vs 0x3D)");
  }
  display.clearDisplay();
  display.setTextSize(1); display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0); display.print("booting"); display.display();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 30000) { delay(400); }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP: "); Serial.println(WiFi.localIP());
    render(String("HUD ready\n") + WiFi.localIP().toString());
    if (MDNS.begin(HOSTNAME)) { MDNS.addService("http", "tcp", 80); }
  } else {
    render("wifi FAIL");
  }

  server.on("/", handleRoot);
  server.on("/msg", HTTP_POST, handleMsg);
  server.begin();
}

void loop() {
  server.handleClient();
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(1000); }
}
