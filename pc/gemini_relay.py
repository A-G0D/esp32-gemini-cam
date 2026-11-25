#!/usr/bin/env python3
# gemini_relay.py - the brain of the rig.
#
#   S3 cam  --(GET /capture)-->  THIS  --(Gemini vision)-->  JSON
#                                  |                            |
#                                  +--(POST /msg text)--> C3 OLED HUD
#
# Loop: grab a JPEG from the ESP32-S3 cam, send it to Gemini with your prompt,
# get JSON back, format it to a few short lines, push those to the C3 OLED.
#
# Setup:  pip install -r requirements.txt
#         copy config.example.json -> config.json and fill it in
# Run:    python gemini_relay.py                 (continuous)
#         python gemini_relay.py --once          (single cycle, for testing)
#         python gemini_relay.py --preview f.jpg (also save the grabbed frame)

import sys, os, json, time, base64, argparse, urllib.request

CFG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_cfg():
    if not os.path.exists(CFG_PATH):
        sys.exit("no config.json - copy config.example.json to config.json and fill it in")
    with open(CFG_PATH) as f:
        return json.load(f)

def http_get_bytes(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()

def http_post_text(url, text, timeout=8):
    data = text.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status

def maybe_resize(jpeg_bytes, width):
    if not width:
        return jpeg_bytes
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        if im.width > width:
            h = int(im.height * width / im.width)
            im = im.resize((width, h), Image.BILINEAR, reducing_gap=2.0)  # ~25% faster than default bicubic
        buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        return jpeg_bytes   # Pillow not installed, send full frame

def _parse_json_text(text):
    text = text.strip()
    if text.startswith("```"):                       # strip stray markdown fences
        text = text.strip("`")
        if text.lower().startswith("json"): text = text[4:]
    return json.loads(text)

def ask_model(cfg, jpeg_bytes):
    if cfg.get("provider", "gemini") == "openai":
        return ask_openai_compat(cfg, jpeg_bytes)    # Groq, OpenRouter, Together, Mistral, local...
    return ask_gemini(cfg, jpeg_bytes)

def ask_gemini(cfg, jpeg_bytes):
    model = cfg.get("model", "gemini-2.5-flash-lite")
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
    body = {
        "contents": [{"parts": [
            {"text": cfg["prompt"]},
            {"inline_data": {"mime_type": "image/jpeg",
                             "data": base64.b64encode(jpeg_bytes).decode("ascii")}},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": cfg.get("temperature", 0.2),
        },
    }
    if cfg.get("response_schema"):
        body["generationConfig"]["responseSchema"] = cfg["response_schema"]

    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": cfg["gemini_api_key"]})
    with urllib.request.urlopen(req, timeout=cfg.get("gemini_timeout", 30)) as r:
        resp = json.loads(r.read())
    return _parse_json_text(resp["candidates"][0]["content"]["parts"][0]["text"])

def ask_openai_compat(cfg, jpeg_bytes):
    # Works with any OpenAI-compatible vision endpoint: Groq, OpenRouter, Together, Mistral, local.
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    body = {
        "model": cfg["openai_model"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": cfg["prompt"]},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
        ]}],
        "temperature": cfg.get("temperature", 0.2),
        "response_format": {"type": "json_object"},   # JSON mode (ignored if unsupported; prompt still asks for JSON)
    }
    url = cfg["openai_base_url"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["openai_api_key"]})
    with urllib.request.urlopen(req, timeout=cfg.get("gemini_timeout", 30)) as r:
        resp = json.loads(r.read())
    return _parse_json_text(resp["choices"][0]["message"]["content"])

def to_display(data, template):
    if not isinstance(data, dict):
        # model ignored the requested JSON object shape (schema isn't enforced
        # on every provider) - show what it sent instead of crashing on .items()
        return str(data)[:40]
    if template:
        class Safe(dict):
            def __missing__(self, k): return "?"
        try:
            return template.format_map(Safe(**data))
        except Exception:
            pass
    lines = ["%s:%s" % (k, v) for k, v in data.items()]   # fallback: key:value
    return "\n".join(lines[:4])

def one_cycle(cfg, preview=None):
    jpeg = http_get_bytes(cfg["s3_capture_url"])
    jpeg = maybe_resize(jpeg, cfg.get("resize_width", 1024))
    if preview:
        with open(preview, "wb") as f: f.write(jpeg)
    data = ask_model(cfg, jpeg)
    text = to_display(data, cfg.get("display_template"))
    http_post_text(cfg["c3_msg_url"], text)
    return data, text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--preview", metavar="FILE", help="save each grabbed frame to FILE")
    args = ap.parse_args()
    cfg = load_cfg()
    interval = cfg.get("interval_sec", 3)

    prov = cfg.get("provider", "gemini")
    mdl = cfg["openai_model"] if prov == "openai" else cfg.get("model", "gemini-2.5-flash-lite")
    print("relay: provider=%s model=%s  s3=%s  c3=%s  every %ss" %
          (prov, mdl, cfg["s3_capture_url"], cfg["c3_msg_url"], interval))
    while True:
        t0 = time.time()
        try:
            data, text = one_cycle(cfg, args.preview)
            print("[ok %.1fs] %s -> %r" % (time.time() - t0, json.dumps(data), text))
        except urllib.error.URLError as e:
            print("[net err]", e)
            try: http_post_text(cfg["c3_msg_url"], "LINK ERR")
            except Exception: pass
        except Exception as e:
            print("[err]", type(e).__name__, e)
            try: http_post_text(cfg["c3_msg_url"], "AI ERR")
            except Exception: pass
        if args.once:
            break
        dt = interval - (time.time() - t0)
        if dt > 0: time.sleep(dt)

if __name__ == "__main__":
    main()
