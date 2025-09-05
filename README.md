# Whats this
Use a Raspberry Pi Pico, running MicroPython, to read the temp and humidity from a DHT sensor, displaying on a OLED screen

# Installation
- Ensure your Pico is running MicroPython
- Copy `lib/` folder to Rpi Pico root
- Copy `main.py` file to Rpi Pico root
  
## Wi‑Fi + HTTP (Pico W)
- Copy `secrets.py.example` to `secrets.py` and fill in `WIFI_SSID` and `WIFI_PASSWORD`.
- With Wi‑Fi configured, the Pico W starts a tiny HTTP server on port 80.
- Visiting the Pico's IP in a browser returns the same temperature/humidity info printed to the terminal.
