#!/usr/bin/env python3
"""Relay round trip: frame in -> HUD text out, stub camera + stub model.

One timed cycle = gemini_relay.one_cycle(): GET /capture from the mock S3 board
(real loopback HTTP, UXGA JPEG), optional Pillow resize, stub model call,
POST /msg to the mock C3 board. Only ask_model is stubbed, so the number is the
PC-side overhead without model latency. Not WiFi, not the camera, not Gemini.

  python3 scripts/bench_relay.py --out results/relay_rtt.json
"""
import argparse, io, json, os, platform, random, statistics, sys, time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pc"))
import gemini_relay as relay
import mock_boards
from PIL import Image


def uxga_jpeg():
    # 1600x1200 (the S3 sketch's HQ_FRAMESIZE) with texture, so the JPEG is a realistic size
    r = random.Random(0)
    small = Image.frombytes("RGB", (200, 150), bytes(r.getrandbits(8) for _ in range(200 * 150 * 3)))
    buf = io.BytesIO()
    small.resize((1600, 1200), Image.BICUBIC).save(buf, "JPEG", quality=80)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--resize", type=int, default=1024, help="resize_width, 0 = send full frame")
    ap.add_argument("--out")
    a = ap.parse_args()

    jpeg = uxga_jpeg()
    mock_boards.S3CamHandler.jpeg = jpeg
    s3, _ = mock_boards.start_server(mock_boards.S3CamHandler)
    c3, _ = mock_boards.start_server(mock_boards.C3HudHandler)
    cfg = {"s3_capture_url": "http://127.0.0.1:%d/capture" % s3.server_address[1],
           "c3_msg_url": "http://127.0.0.1:%d/msg" % c3.server_address[1],
           "resize_width": a.resize, "display_template": "{target}"}

    ms = []
    with mock.patch.object(relay, "ask_model", return_value={"target": "cat"}):
        for i in range(a.n + 5):
            t = time.perf_counter()
            _, text = relay.one_cycle(cfg)
            dt = (time.perf_counter() - t) * 1000
            assert text == "cat" and mock_boards.C3HudHandler.last_text == "cat"
            if i >= 5:  # 5 warmup cycles
                ms.append(dt)
    ms.sort()
    res = {"n": len(ms), "resize_width": a.resize, "frame_bytes": len(jpeg),
           "median_ms": round(statistics.median(ms), 2), "mean_ms": round(statistics.fmean(ms), 2),
           "p90_ms": round(ms[int(len(ms) * 0.9)], 2), "max_ms": round(ms[-1], 2),
           "machine": "%s %s, Python %s" % (platform.system(), platform.machine(), platform.python_version())}
    print(json.dumps(res, indent=1))
    if a.out:
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
