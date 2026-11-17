[README.md](https://github.com/user-attachments/files/34997303/README.md)
# canedge2-logger

Pipeline for logging the Gopher Motorsports car with a **CSS Electronics CANedge2** and getting that data into the existing tools (gopher-vision, MoTeC `.ld`).

## What the CANedge2 changes

`DataLogger_25` is *our* STM32 board: it runs GopherCAN, decodes every parameter itself and writes `.gdat` to an SD card.
The CANedge2 is a finished product with closed firmware. It does **no** GopherCAN decoding: it records raw CAN frames (ID + 8 bytes + timestamp) to `.MF4` files.
So there is no embedded code to write. The decode step moves from firmware to Python:

```
car ─ GCAN0/GCAN1 ─► CANedge2 ─ SD card / WiFi ─► *.MF4 ─► canedge2 (this repo) ─► .gdat ─► gopher-vision ─► .ld (MoTeC i2)
                     (raw frames)                           decode with go4-XX.yaml        (existing)        .csv (full fidelity)
```

The **GopherCAN network YAML** (`gophercan-lib/network_autogen/configs/go4-XX.yaml`) stays the single source of truth: firmware, gopher-vision and this repo all read the same file.

## Layout

| file | role |
|---|---|
| `canedge2/network.py` | loads the YAML the same way `autogen.py` does; `lint()` flags config problems |
| `canedge2/codec.py` | decode/encode group payloads, mirrors `GopherCAN.c` |
| `canedge2/frames.py` | `RawFrame` + CSV frame format (no hardware / MF4 lib needed) |
| `canedge2/mf4_source.py` | MF4 reader (asammdf). **Unverified until a real file exists** |
| `canedge2/decode.py` | frames → timestamped samples + statistics |
| `canedge2/gdat.py` | `.gdat` writer (gopher-vision compatible) and long-format CSV |
| `canedge2/sessions.py` | finds `DEVICE/SESSION/SPLIT.MF4` files, groups them into sessions (1 session = 1 power cycle ≈ 1 drive); S3 mirror helper |
| `canedge2/convert.py` | CLI: files or a session folder → one `.gdat` / `.csv` / `.ld` with a single time base |
| `canedge2/watch.py` | pit-lane watcher: converts each finished drive, optional live push to gopher-vision over UDP |
| `canedge2/sim.py` | synthetic traffic generator for testing |
| `canedge2/deviceconfig.py` + `config/canedge2_settings.yaml` | re-applies our CANedge2 settings to a config file |
| `tests/` | codec, pipeline, and an end-to-end test against gopher-vision's own parser |

## Use it now (no device needed)

```bash
pip install pyyaml numpy                      # + asammdf for real MF4 files
export GCAN_CONFIG=../gophercan-lib/network_autogen/configs/go4-26.yaml
python -m canedge2.sim --config $GCAN_CONFIG --out sim.csv --seconds 10
python -m canedge2.convert sim.csv --config $GCAN_CONFIG --out out/ --format gdat csv ld --gopher-vision ../gopher-vision
python -m unittest discover -s tests          # GOPHER_VISION=../gopher-vision to include the e2e test

# a drive that the logger split into several files -> pass the session folder
python -m canedge2.convert LOGS/3B912722/00000004 --config $GCAN_CONFIG --out out/ --format gdat ld --gopher-vision ../gopher-vision
```

## When the device arrives (in this order)

1. **Format check** – copy one `.MF4` over and run `python -m canedge2.mf4_source file.MF4`. Confirm `CAN_DataFrame.ID / IDE / BusChannel / DataBytes / DataLength` exist; fix `mf4_source.py` if names or `BUS_BASE` differ. This is the only untested code path.
2. **Config** – set CAN1/CAN2 to 1 Mbit/s and **monitoring** (no-transmit) mode in CSS's config editor; save; copy the JSON paths into `config/canedge2_settings.yaml`.
3. **Bench loopback** – run `canedge2.sim` frames onto a bench bus (e.g. from a spare GopherCAN board) and diff the logger's MF4 against the CSV.
4. **Time** – confirm the CANedge RTC is set (it timestamps in UTC, unlike `DataLogger_25`, which counts ms from boot). Check file start times against a phone clock.
5. **Bus count** – see below.

## Known issues found while reading the repos

* **go4-26 has three buses, the CANedge2 has two channels.** In go4-26.yaml Telemetry sits only on `GCAN2`, along with RVC/BMS/IMU/Display/SWM/CCB/PDM. `DataLogger_25` was wired to GCAN0+GCAN1 (go4-25 layout). Decide which two buses to tap, or use two loggers. Frames seen on several buses are *not* de-duplicated.
* **Duplicate `motec_name`s in go4-26.yaml** (13 names, e.g. `isMotorStill_FL_state` reuses "FL Motor Position (deg)"). gopher-vision keys `.ld` channels by that name, so duplicates silently collapse. `lint()` prints these.
* **`gpsSpeed_mph`, `gpsCourse_deg`** are UNSIGNED16 with scale 0.01; the firmware casts scale to an integer, so on-car they read 0. This repo decodes the intended value (`--firmware-quirks` reproduces the old behaviour). They can't be stored in `.gdat`; use the CSV output.
* `go4-26.dbc` disagrees with `go4-26.yaml` on 16 signals (scales, one steering-angle offset). Regenerate DBCs from the YAML (`DBC_converter_v3.py`) before using them with CSS tools.
* `gopher-vision/db.py` has a Supabase key committed to the repo.

## Replacing DataLogger_25 entirely

What the old board did, and what covers it now:

| old logger | with the CANedge2 only |
|---|---|
| Listen to GCAN0/1, decode every parameter | CANedge2 records raw frames; `canedge2.convert` decodes afterwards |
| Write `.gdat` to SD | `.MF4` on SD (and WiFi upload); `.gdat`/`.ld` produced by `canedge2.convert` |
| RTC for file names | CANedge2's own RTC (UTC). Set it and verify before the first test day |
| `tm_*` health parameters (SD fill, dropped packets) | gone; the CANedge2 reports its own status (CAN heartbeat / WiFi status file, if enabled) |
| Live telemetry | **not covered.** The uploaded `DataLogger_25` has no radio transmit code (only a debug UART), so if live data exists today something else produces it; find out what before removing the board |
| `LOG_COMPLETE` heartbeat for sensor-board LEDs | Gopher_Sense's handler exists but its registration is commented out (`gopher_sense.c:128`) and DataLogger_25 never sends the command, so nothing changes in this copy of the code |

Cleanup in gophercan-lib: the Telemetry module (ID 10) and its `tm_*` parameters stay in the YAML until you decide to retire them; nothing will transmit them.


## Pit-lane setup (laptop)

```
car: CANedge2 ──WiFi──► pit-lane access point ──► laptop: MinIO (S3) ─► canedge2.watch ─► data/<device>/<device>_<session>.gdat/.ld
                                                                              └──UDP──► gopher-vision live view (latest values)
```

1. **Laptop:** run a MinIO server on the pit network (the CANedge2 uploads over HTTP/HTTPS to an S3-compatible server).
2. **CANedge2 config:** WiFi access point, S3 endpoint/bucket/keys (use CSS's encryption tool for the credentials) and a file split period. Shorter splits = fresher data, more, smaller files.
3. **Watcher:**
   ```bash
   python -m canedge2.watch LOGS/ --config $GCAN_CONFIG --out data/ --format gdat ld --gopher-vision ../gopher-vision \
       --idle 120 --udp 127.0.0.1:5000 \
       --s3-bucket BUCKET --s3-endpoint http://localhost:9000 --s3-key KEY --s3-secret SECRET
   ```
   Without `--s3-*` it just watches a folder (e.g. a copy of the SD card's `LOG/`).
4. **Live view:** in gopher-vision choose socket mode on `127.0.0.1:5000`. It shows the *latest* value per parameter from each newly uploaded file, so latency ≈ split period + upload time. It is near-real-time, not a radio link.

Tested: session grouping, split ordering (2 before 10), one-stream decode across files, idle/re-convert logic, UDP datagram sizing.
**Untested (needs the device/server):** MF4 reading, the S3/MinIO mirror (`sessions.sync_s3`), and when the logger uploads the last split of a power cycle (the `--idle` default is a guess; tune it on the first real drives).
