Project Brief
- Purpose: MicroPython app for Raspberry Pi Pico W that samples temperature and humidity via a DHT sensor once per second, shows values on a 128x64 SSD1306 OLED, prints a concise status to serial, and serves a tiny web dashboard with a live graph and JSON API.
- Entrypoint: `main.py` runs directly on the board. No package layout; a flat script plus one driver under `lib/`.
- Constraints: Runs under MicroPython with tight RAM/CPU; avoid heavy libs, large allocations, and blocking I/O.

Hardware
- Board: Raspberry Pi Pico W (Wi‑Fi used if credentials exist).
- Sensor: DHT11 on `GPIO6` (1 Hz sampling). `main.py:167`
  - Alternate (commented): DHT22 on `GPIO22`. `main.py:166`
- OLED: SSD1306 `128x64` over I2C0 (`SDA=GPIO0`, `SCL=GPIO1`, 400 kHz). `main.py:172-176`
- LEDs: Discrete RGB on `GPIO18` (G), `GPIO19` (R), `GPIO20` (B). `main.py:168-170`

Files
- `main.py`: Complete application (sensors, OLED UI, circular buffers, HTTP server, HTML/JS dashboard).
- `lib/ssd1306.py`: Display driver used by `main.py`.
- `secrets.py.example`: Template copied to `secrets.py` on the device to enable Wi‑Fi.
- `README.md`: Quickstart; some values drift from current code (see Notes).

Configuration
- Wi‑Fi: Provide `secrets.py` alongside `main.py` with `WIFI_SSID` and `WIFI_PASSWORD`. `main.py:20-28`
- HTTP history limits: `POINTS_DEFAULT=900`, `POINTS_MAX=3600` (i.e., up to 1 hour at 1 Hz). `main.py:52-54`
- Sampling cadence: `sleep(1)` in main loop; keep at 1 s for buffer math. `main.py:318-321`
- History windows (seconds): 5m, 10m, 30m, 60m; used for on‑device display. `main.py:197-201`

Runtime Behavior
- Loop: Every second, measure DHT, update circular buffers, compute simple moving averages, refresh OLED, print a two‑line status to serial, and service a single HTTP request if a connection is pending. `main.py:316-433`
- Circular buffers: Fixed size `buffer_size=3601` for both temperature and humidity, indexed modulo `buffer_size`. `main.py:213-217`
- Status text: Compact textual snapshot with current, average, and t‑minus values. Built by `build_status_text(t,h)`. `main.py:237-241`
- RGB LED policy: Temperature bands light B/G/R combinations; see thresholds in the loop. `main.py:333-355`
- Memory log: Prints `gc.mem_free()` periodically for visibility. `main.py:428`

HTTP Server
- Startup: If Wi‑Fi connects at boot, a non‑blocking HTTP server binds `0.0.0.0:80`. `main.py:225-233`, `main.py:56-76`
- Polling: Each loop, try `accept()` and handle at most one client quickly. `main.py:78-163`, `main.py:407-413`
- Endpoints:
  - `/` → HTML dashboard with a canvas chart and simple decimation/banding for large histories. `main.py:259-314`
  - `/data?points=N` → JSON `{ "t": [°C], "h": [%] }` with the most recent `N` samples, clamped to `[10, POINTS_MAX]` and the number of captured readings. `main.py:108-123`, `main.py:243-257`
  - `/text` → Plain text status (two lines), same content as serial (without memory line). `main.py:132-141`, `main.py:237-241`
- HTML/JS behavior: Polls `/data` every 2s, lets user set visible point count or hours, performs optional min/max banding decimation to reduce draw cost. `main.py:271-313`

OLED Rendering
- Layout: Two lines showing current temp and humidity plus 5/10/30/60‑minute values. `main.py:403-405`
- Burn‑in mitigation: Shifts text position every ~30 seconds to a small random offset. `main.py:361-367`

Data & Timing
- Sampling rate: 1 Hz; changing it requires coordinated updates to buffer sizing, HTTP `POINTS_MAX`, and “minutes ago” indexing.
- Buffers: Last `N` seconds are stored; `/data` slices recent contiguous data ending at the most recent completed sample. `main.py:250-257`
- Averages: Simple exponential blend `avg = (avg + latest)/2` retained for display; not used by graph data.

Security & Networking
- No TLS or auth; intended for trusted LANs only. Avoid exposing port 80 to the internet.
- Wi‑Fi reconnect: If credentials exist and HTTP is down, retry about every 30 seconds to re‑establish Wi‑Fi/server. `main.py:414-422`

Developing Changes (for AI Assistants)
- Target: MicroPython. Prefer `ujson`, `usocket`; fall back gracefully when unavailable. `main.py:7-14`
- Keep memory churn low: Reuse buffers, avoid building large strings in loops, keep HTML/CSS/JS compact. The current HTML is an embedded minimal string.
- Non‑blocking I/O: HTTP accept/read is short and bounded; keep it quick to not delay sensor sampling.
- Don’t break 1 Hz cadence: OLED updates, networking, and parsing must stay under ~1 s average.
- Libraries: Only `lib/ssd1306.py` is bundled; avoid adding extra modules unless small and justified.
- Style: Keep changes localized. Avoid adding imports not present in MicroPython. No type‑heavy constructs or f‑strings in hot loops that allocate excessively beyond current usage.
- Pin configurability: If you add config, prefer small constants near current definitions and keep defaults matching present wiring.

Extensibility Ideas
- Config file: Optional `config.py` for sensor type (DHT11 vs DHT22), pins, and display options.
- Sensor abstraction: Wrap DHT in a small adapter to ease swapping sensors without touching the loop.
- Persistence: Optionally persist downsampled hourly/daily aggregates to flash (mind wear leveling and size).
- Health endpoint: Add `/text` fields for uptime and free memory, or a dedicated `/status` JSON.
- Power: Dim or power off OLED after inactivity windows; already scaffolded with commented code. `main.py:391-398`

Deployment
- Flash MicroPython firmware to the Pico W.
- Copy `lib/` and `main.py` to device root; copy `secrets.py.example` to `secrets.py` with Wi‑Fi credentials.
- Reboot; monitor serial for “HTTP server listening on :80” and memory/reading logs.
- Visit `http://<pico-ip>/` on the LAN.

Troubleshooting
- No display: Verify I2C wiring and that your SSD1306 address matches the driver defaults (commonly 0x3C). Pins must be `GPIO0=SDA`, `GPIO1=SCL` by default here.
- Bad sensor reads: DHT sensors are finnicky; ensure proper pull‑up and wiring; occasional `OSError` is caught and logged.
- No Wi‑Fi: Ensure `secrets.py` exists and is valid; check serial logs for IP via `wlan.ifconfig()`.
- Outdated README values: README references `POINTS_DEFAULT=300` and `POINTS_MAX=1200`, but code uses 900/3600. Prefer `main.py` as source of truth.

Key Code References
- HTTP points limits: `main.py:52-54`, `main.py:119-123`
- Circular buffers and index: `main.py:213-217`, `main.py:388-389`
- `/data` serialization: `main.py:243-257`
- OLED updates: `main.py:400-405`
- LED thresholds: `main.py:333-355`
- Wi‑Fi connect + server: `main.py:30-47`, `main.py:56-76`, `main.py:225-233`

Notes
- Keep the event loop lean; large blocking operations will drop samples and make the UI stutter.
- If increasing `POINTS_MAX`, also grow `buffer_size` accordingly and consider HTML canvas performance on weak clients.
