// s3_cam.ino v2 - ESP32-S3 CAM: blur-gated two-mode capture for the Gemini relay.
//
// SCAN mode: fast low-res frames, watched (not sent) to judge focus + steadiness.
// When a frame is sharp AND steady, the board bumps to full resolution, grabs ONE
// crisp HQ frame, and holds it. Only that HQ frame is served to the PC. Blurry or
// moving frames never leave the board -> no wasted Gemini calls, better answers.
//
// Focus metric = JPEG byte size (sharp/detailed compresses bigger, blur/motion
// smaller). Steady = that size holding stable across the last few frames. This is
// a proxy (the standard ESP32-CAM autofocus trick), tune SHARP_MIN on real hw
// using GET /status which reports the live number.
//
// Endpoints:
//   GET /                 status text
//   GET /capture          the latest sharp HQ frame (JPEG). 503 if none yet.
//   GET /capture?fresh=1  wait (up to timeout) for a NEW sharp frame, then return it.
//   GET /status           JSON { scan_len, sharp_min, stable, have_shot, age_ms, rssi, heap }
//
// Board: "ESP32S3 Dev Module", PSRAM: OPI PSRAM, Flash 16MB. See README.

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

// ---------------------------------------------------------------- config
// WIFI_SSID / WIFI_PASS come from wifi_config.h (gitignored). Copy
// wifi_config.example.h -> wifi_config.h in this folder and fill it in.
#include "wifi_config.h"
const char* HOSTNAME  = "s3cam";

#define SCAN_FRAMESIZE  FRAMESIZE_QVGA    // 320x240, fast preview for gating
#define HQ_FRAMESIZE    FRAMESIZE_UXGA    // 1600x1200 crisp shot for Gemini
#define SCAN_QUALITY    12
#define HQ_QUALITY      8                 // lower number = higher quality

int      SHARP_MIN     = 9000;            // min scan JPEG bytes to count as "sharp" (TUNE via /status)
float    STABLE_SPREAD = 0.15;            // max (max-min)/mean over recent frames to be "steady"
uint32_t RETRIGGER_MS  = 1500;            // min gap between HQ shots (~ your "2 fps" clarity cadence)

#define BATTERY_ADC_PIN  -1
#define BATTERY_DIVIDER  2.0

// ---------------------------------------------------------------- camera pins
// Default = Freenove ESP32-S3-WROOM CAM. VERIFY against YOUR board and edit.
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  15
#define SIOD_GPIO_NUM   4
#define SIOC_GPIO_NUM   5
#define Y9_GPIO_NUM    16
#define Y8_GPIO_NUM    17
#define Y7_GPIO_NUM    18
#define Y6_GPIO_NUM    12
#define Y5_GPIO_NUM    10
#define Y4_GPIO_NUM     8
#define Y3_GPIO_NUM     9
#define Y2_GPIO_NUM    11
#define VSYNC_GPIO_NUM  6
#define HREF_GPIO_NUM   7
#define PCLK_GPIO_NUM  13

WebServer server(80);

// held sharp HQ shot (in PSRAM)
uint8_t*  g_shot = nullptr;
size_t    g_shot_len = 0;
uint32_t  g_shot_time = 0;

// scan state
int      g_lens[5] = {0,0,0,0,0};
int      g_li = 0;
int      g_scan_len = 0;
bool     g_stable = false;
uint32_t g_last_hq = 0;

bool initCamera() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0=Y2_GPIO_NUM; c.pin_d1=Y3_GPIO_NUM; c.pin_d2=Y4_GPIO_NUM; c.pin_d3=Y5_GPIO_NUM;
  c.pin_d4=Y6_GPIO_NUM; c.pin_d5=Y7_GPIO_NUM; c.pin_d6=Y8_GPIO_NUM; c.pin_d7=Y9_GPIO_NUM;
  c.pin_xclk=XCLK_GPIO_NUM; c.pin_pclk=PCLK_GPIO_NUM;
  c.pin_vsync=VSYNC_GPIO_NUM; c.pin_href=HREF_GPIO_NUM;
  c.pin_sccb_sda=SIOD_GPIO_NUM; c.pin_sccb_scl=SIOC_GPIO_NUM;
  c.pin_pwdn=PWDN_GPIO_NUM; c.pin_reset=RESET_GPIO_NUM;
  c.xclk_freq_hz=20000000;
  c.pixel_format=PIXFORMAT_JPEG;
  c.frame_size=SCAN_FRAMESIZE;              // start in scan mode
  c.jpeg_quality=SCAN_QUALITY;
  c.fb_count=2;
  c.fb_location=CAMERA_FB_IN_PSRAM;
  c.grab_mode=CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { Serial.printf("camera init failed 0x%x\n", err); return false; }
  return true;
}

void setSize(framesize_t fs, int q) {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) return;
  s->set_framesize(s, fs);
  s->set_quality(s, q);
  // discard a couple frames so the sensor settles into the new size
  for (int i=0;i<2;i++){ camera_fb_t* fb=esp_camera_fb_get(); if(fb) esp_camera_fb_return(fb); }
}

// grab one full-res frame and hold it as the latest sharp shot
bool captureHQ() {
  setSize(HQ_FRAMESIZE, HQ_QUALITY);
  camera_fb_t* fb = esp_camera_fb_get();
  bool ok = false;
  if (fb) {
    uint8_t* nb = (uint8_t*) ps_realloc(g_shot, fb->len);
    if (nb) {
      g_shot = nb;
      memcpy(g_shot, fb->buf, fb->len);
      g_shot_len = fb->len;
      g_shot_time = millis();
      ok = true;
    }
    esp_camera_fb_return(fb);
  }
  setSize(SCAN_FRAMESIZE, SCAN_QUALITY);   // back to fast scanning
  g_last_hq = millis();
  return ok;
}

// one scan frame: update focus metric + steadiness, trigger HQ when sharp+steady
void scanTick() {
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) return;
  g_scan_len = fb->len;
  esp_camera_fb_return(fb);

  g_lens[g_li] = g_scan_len; g_li = (g_li + 1) % 5;
  int mn=999999, mx=0, sum=0, n=0;
  for (int i=0;i<5;i++){ if(g_lens[i]>0){ mn=min(mn,g_lens[i]); mx=max(mx,g_lens[i]); sum+=g_lens[i]; n++; } }
  if (n < 5) { g_stable=false; return; }
  int mean = sum/n;
  g_stable = ((float)(mx-mn)/mean) < STABLE_SPREAD;

  bool sharp = mean >= SHARP_MIN;
  if (sharp && g_stable && millis() - g_last_hq > RETRIGGER_MS) {
    captureHQ();
  }
}

float readBattery() {
#if BATTERY_ADC_PIN >= 0
  return (analogReadMilliVolts(BATTERY_ADC_PIN)/1000.0)*BATTERY_DIVIDER;
#else
  return -1;
#endif
}

void sendShot() {
  if (!g_shot || g_shot_len == 0) { server.send(503, "text/plain", "no sharp shot yet"); return; }
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.setContentLength(g_shot_len);
  server.send(200, "image/jpeg", "");
  server.sendContent((const char*)g_shot, g_shot_len);
}

void handleCapture() {
  if (server.arg("fresh") == "1") {
    uint32_t since = g_shot_time;
    uint32_t t0 = millis();
    while (g_shot_time == since && millis() - t0 < 6000) {  // wait for a NEW sharp shot
      scanTick();
      delay(5);
    }
  }
  sendShot();
}

void handleStatus() {
  char buf[192];
  snprintf(buf, sizeof(buf),
    "{\"scan_len\":%d,\"sharp_min\":%d,\"stable\":%s,\"have_shot\":%s,\"age_ms\":%lu,\"rssi\":%d,\"heap\":%u}",
    g_scan_len, SHARP_MIN, g_stable?"true":"false", g_shot?"true":"false",
    g_shot?(unsigned long)(millis()-g_shot_time):0, WiFi.RSSI(), (unsigned)ESP.getFreeHeap());
  server.send(200, "application/json", buf);
}

void handleRoot() {
  server.send(200, "text/plain",
    String("S3 cam v2 (blur-gated). GET /capture for the latest sharp shot. IP ")+WiFi.localIP().toString());
}

void setup() {
  Serial.begin(115200); delay(300);
  if (!initCamera()) Serial.println("HALT: camera");

  WiFi.mode(WIFI_STA); WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t t0=millis();
  while (WiFi.status()!=WL_CONNECTED && millis()-t0<30000){ delay(400); Serial.print("."); }
  Serial.println();
  if (WiFi.status()==WL_CONNECTED){
    Serial.print("IP: "); Serial.println(WiFi.localIP());
    if (MDNS.begin(HOSTNAME)){ MDNS.addService("http","tcp",80); Serial.println("mdns: s3cam.local"); }
  } else Serial.println("wifi FAILED");

  server.on("/", handleRoot);
  server.on("/capture", handleCapture);
  server.on("/status", handleStatus);
  server.begin();
}

void loop() {
  server.handleClient();
  scanTick();                              // continuously watch for a sharp, steady moment
  if (WiFi.status()!=WL_CONNECTED){ WiFi.reconnect(); delay(1000); }
}
