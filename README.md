# Whats this
Use a Raspberry Pi Pico, running MicroPython, to read the temp and humidity from a DHT sensor, displaying on a OLED screen

# Installation
- Ensure your Pico is running MicroPython
- Copy `lib/` folder to Rpi Pico root
- Copy `main.py` file to Rpi Pico root
  
## Wi‑Fi + HTTP (Pico W)
- Copy `secrets.py.example` to `secrets.py` and fill in `WIFI_SSID` and `WIFI_PASSWORD`.
- With Wi‑Fi configured, the Pico W starts a tiny HTTP server on port 80.
- Visit `http://<pico-ip>/` for a lightweight canvas graph of recent readings.
- Use the "Points" input to change history length (default 300, max 1200).
- API endpoints:
  - `/data?points=N` → JSON `{t:[...], h:[...]}` of last N samples.
  - `/text` → plain text status lines (same as serial output without memory line).
