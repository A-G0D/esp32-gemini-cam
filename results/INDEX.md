# Results index

| README number | File | Command |
|---|---|---|
| Relay median 35.2 ms (before, 1024 px resize, median of 3 runs) | results/relay_rtt_before.json | `python3 scripts/bench_relay.py --out results/relay_rtt_before.json` on the previous `maybe_resize` (default bicubic) |
| Relay median 31.1 ms (after) | results/relay_rtt.json | `python3 scripts/bench_relay.py --out results/relay_rtt.json` |
| Relay median 0.6 ms (no resize) | results/relay_rtt_noresize.json | `python3 scripts/bench_relay.py --resize 0 --out results/relay_rtt_noresize.json` |

The JSON files hold one run each; the README figures are the median of three runs per variant, so the files may differ from the README by a few ms.
