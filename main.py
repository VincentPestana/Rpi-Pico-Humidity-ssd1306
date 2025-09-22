# Complete project details at https://RandomNerdTutorials.com/raspberry-pi-pico-dht11-dht22-micropython/

from machine import Pin, I2C
from time import sleep, ticks_ms, ticks_diff
import machine
import network
try:
    import usocket as socket  # MicroPython
except ImportError:
    import socket  # Fallback
try:
    import ujson as json
except Exception:
    import json
import dht
from ssd1306 import SSD1306_I2C
import random
import gc

# ---- WiFi configuration ----
# Create a file `secrets.py` next to this file with:
# WIFI_SSID = "your-ssid"
# WIFI_PASSWORD = "your-password"
try:
    from secrets import WIFI_SSID, WIFI_PASSWORD  # type: ignore
except Exception:
    WIFI_SSID = None
    WIFI_PASSWORD = None

def connect_wifi(ssid: str, password: str, retries: int = 20):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to WiFi…")
        wlan.connect(ssid, password)
        attempts = 0
        while not wlan.isconnected() and attempts < retries:
            sleep(0.5)
            attempts += 1
            if attempts % 4 == 0:
                print("…waiting for connection")
    if wlan.isconnected():
        print("WiFi connected:", wlan.ifconfig())
        return wlan
    print("WiFi connect failed")
    return None

# HTTP server (non-blocking accept inside the main loop)
server_sock = None

# Configurable history for HTTP /data (points of recent seconds)
# Points now represent minutes of averaged data
POINTS_DEFAULT = 60   # 1 hour of minute-averaged samples
POINTS_MAX = 1440     # up to 24 hours of history

def start_http_server():
    global server_sock
    if server_sock:
        return server_sock
    try:
        s = socket.socket()
        try:
            # Not always available, best-effort
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        s.bind(("0.0.0.0", 80))
        s.listen(2)
        s.settimeout(0)  # non-blocking accept
        server_sock = s
        print("HTTP server listening on :80")
        return s
    except Exception as e:
        print("HTTP server error:", e)
        server_sock = None
        return None

def _send_all(conn, data):
    """Reliable send that handles partial writes.
    Accepts str or bytes; encodes str as UTF-8 once, then loops until sent.
    """
    try:
        if isinstance(data, str):
            data = data.encode('utf-8')
        mv = memoryview(data)
        total = len(mv)
        sent = 0
        while sent < total:
            n = conn.send(mv[sent:])
            if n is None:
                # Some stacks return None on non-blocking progress; treat as 0
                n = 0
            if n <= 0:
                # Avoid tight spin; yield briefly
                sleep(0.001)
                continue
            sent += n
    except Exception:
        # Re-raise to let caller decide how to handle
        raise

def http_poll_and_respond(build_text, build_json, build_html):
    """Try to accept a single connection and respond; non-blocking.
    build_text(): returns plain text status
    build_json(points:int): returns JSON string with recent data
    build_html(): returns HTML dashboard page
    """
    global server_sock
    if not server_sock:
        return
    try:
        conn, addr = server_sock.accept()
    except Exception:
        return  # nothing to accept right now
    try:
        conn.settimeout(0.5)
        req = conn.recv(512)  # Read request head
        if not req:
            return
        # Parse very small subset of HTTP
        try:
            first = req.split(b"\r\n", 1)[0]
            parts = first.split()
            method = parts[0].decode()
            target = parts[1].decode() if len(parts) > 1 else "/"
        except Exception:
            method = "GET"
            target = "/"

        path, _, query = target.partition("?")

        if path == "/data":
            # Parse points=N
            points = POINTS_DEFAULT
            if query:
                for kv in query.split("&"):
                    k, _, v = kv.partition("=")
                    if k == "points":
                        try:
                            points = int(v)
                        except Exception:
                            points = POINTS_DEFAULT
            if points < 10:
                points = 10
            if points > POINTS_MAX:
                points = POINTS_MAX
            payload = build_json(points)
            hdr = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                "Connection: close\r\n"
                "Cache-Control: no-store\r\n\r\n"
            )
            _send_all(conn, hdr)
            # Allow a little more time for larger JSON bodies
            try:
                conn.settimeout(2)
            except Exception:
                pass
            _send_all(conn, payload)
        elif path == "/text":
            payload = build_text()
            hdr = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain; charset=utf-8\r\n"
                "Connection: close\r\n"
                "Cache-Control: no-store\r\n\r\n"
            )
            _send_all(conn, hdr)
            _send_all(conn, payload)
        else:
            # default dashboard
            payload = build_html()
            hdr = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Connection: close\r\n"
                "Cache-Control: no-store\r\n\r\n"
            )
            _send_all(conn, hdr)
            _send_all(conn, payload)
    except Exception as e:
        try:
            conn.send("HTTP/1.1 500 Internal Server Error\r\nConnection: close\r\n\r\n")
        except Exception:
            pass
        print("HTTP handler error:", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

# DHT Sensor
#sensor = dht.DHT22(Pin(22))
sensor = dht.DHT11(Pin(6))
gled = machine.Pin(18, machine.Pin.OUT)
rled = machine.Pin(19, machine.Pin.OUT)
bled = machine.Pin(20, machine.Pin.OUT)

# OLED Screen
WIDTH =128 
HEIGHT= 64
i2c=I2C(0,sda=Pin(0), scl=Pin(1), freq=400000)
oled = SSD1306_I2C(WIDTH,HEIGHT,i2c)

# Variables
lowestTemp = 40 # Lowest temp the RGB cares about
lowTemp = 45 # Highest temp the RGB cares about

# Initialize variables
sleepCount = 0
displayMoveCount = 0
averageCount = 0
averageCount30m = 0
avgTemp30m = 0
avgHum30m = 0

randomX = 0
randomY = 0
avgTemp = 0
avgHum = 0
avgTemp60s = 0
avgHum60s = 0

# Minute aggregation accumulators
minute_temp_sum = 0.0
minute_hum_sum = 0.0
minute_sample_count = 0

# OLED burn-in mitigation: jitter settings
# Move the on-screen text every N seconds to spread pixel wear
JITTER_SECONDS = 10  # default ~10s; keep small to reduce static image time
JITTER_X_MAX = 100    # horizontal jitter range (pixels)
JITTER_Y_MAX = 45    # vertical jitter range (pixels; allow two 8px lines)

# OLED power cycling to mitigate burn-in: 15s off, 15s on
OLED_ON_SECONDS = 15
OLED_OFF_SECONDS = 15
oled_on = True
oled_phase_count = 0

# Sampling/aggregation constants
SAMPLES_PER_MINUTE = 60
MINUTES_5 = 5
MINUTES_10 = 10
MINUTES_30 = 30
MINUTES_60 = 60

# Convenience conversion for the hour-scale second counter
SECONDS_60M = MINUTES_60 * SAMPLES_PER_MINUTE

# Values 5, 10 and 30 minutes ago
temp5m = ''
hum5m = ''
temp10m = ''
hum10m = ''
temp30m = ''
hum30m = ''
temp60m = ''
hum60m = ''

# Fixed-size circular buffers, for storing the humidity and temperature readings
# Minute-averaged circular buffer (slightly oversized for wrap clarity)
buffer_size = POINTS_MAX + 1
tempList = [0] * buffer_size
humList = [0] * buffer_size
current_index = 0

# Turn all LED's off
def led_off():
    gled.low()
    rled.low()
    bled.low()

# Small OLED helper for brief status messages at boot
def _oled_status(line1: str, line2: str = "", line3: str = ""):
    try:
        oled.fill(0)
        oled.contrast(1)
        if line1:
            oled.text(line1, 0, 0)
        if line2:
            oled.text(line2, 0, 10)
        if line3:
            oled.text(line3, 0, 20)
        oled.show()
    except Exception:
        # If display isn't ready or errors, ignore silently
        pass

# Try WiFi + HTTP once at boot if credentials exist
wlan = None
if WIFI_SSID and WIFI_PASSWORD:
    # Show quick connection status on OLED
    _oled_status("Connecting to WiFi", "...please wait")
    wlan = connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if wlan and wlan.isconnected():
        # Try to gather connection details
        ip = None
        rssi = None
        try:
            ip = wlan.ifconfig()[0]
        except Exception:
            ip = None
        try:
            # RSSI may not be supported on all ports
            rssi = wlan.status('rssi')
        except Exception:
            rssi = None
        # Show IP on line 2; RSSI on line 3 if available
        line2 = ("IP:" + ip) if ip else ""
        line3 = ("RSSI:{} dBm".format(rssi)) if rssi is not None else ""
        _oled_status("WiFi Connected", line2, line3)
        # Keep the message visible ~10 seconds
        for _ in range(10):
            sleep(1)
        start_http_server()
    else:
        _oled_status("WiFi connect failed", "HTTP disabled")
else:
    _oled_status("No WiFi credentials", "HTTP disabled")
    print("No WiFi credentials found in secrets.py; HTTP disabled.")

# Track how many minute-averaged readings we have captured (for data endpoint)
readings_count = 0

# --- Lightweight memory stats helper ---
# Tracks free low-water and prints a compact line every ~30 seconds.
mem_min_free = gc.mem_free()
mem_last_report_sec = -1
mem_stats_line = ""

def _format_mem_line(alloc, free, low):
    total = alloc + free
    # Use integer KB to stay compact and avoid float formatting
    return "Mem used/free/total: {}K/{}K/{}K (low {}K free)".format(
        alloc // 1024, free // 1024, total // 1024, low // 1024
    )

def mem_update_and_maybe_log(current_seconds):
    global mem_min_free, mem_last_report_sec, mem_stats_line
    # Track low-water continuously with minimal churn
    free_now = gc.mem_free()
    if free_now < mem_min_free:
        mem_min_free = free_now
    # Emit a line every ~30s; also refresh the cached text
    if current_seconds % 30 == 0 and current_seconds != mem_last_report_sec:
        # Collect before sampling for a stable reading; do it sparsely
        gc.collect()
        alloc = gc.mem_alloc()
        free = gc.mem_free()
        base = _format_mem_line(alloc, free, mem_min_free)
        # Append buffer lengths and filled count
        filled = readings_count if readings_count < buffer_size else buffer_size
        mem_stats_line = base + " | samples(min):{}/{}".format(filled, buffer_size)
        print(mem_stats_line)
        mem_last_report_sec = current_seconds


def _hist_str(val):
    if isinstance(val, (int, float)):
        return "{:.1f}".format(val)
    return val if val else "--"

def build_status_text(t, h):
    # Include latest memory snapshot as a third line; if none yet, build one ad-hoc
    global mem_stats_line
    if not mem_stats_line:
        alloc = gc.mem_alloc()
        free = gc.mem_free()
        base = _format_mem_line(alloc, free, mem_min_free)
        filled = readings_count if readings_count < buffer_size else buffer_size
        mem_stats_line = base + " | samples(min):{}/{}".format(filled, buffer_size)
    t5 = _hist_str(temp5m)
    t10 = _hist_str(temp10m)
    t30 = _hist_str(temp30m)
    t60 = _hist_str(temp60m)
    h5 = _hist_str(hum5m)
    h10 = _hist_str(hum10m)
    h30 = _hist_str(hum30m)
    h60 = _hist_str(hum60m)
    return (
        f"T: {t}c {avgTemp:.0f} {t5} {t10} {t30} {t60}\n"
        f"H: {h}% {avgHum:.0f} {h5} {h10} {h30} {h60}\n"
        + mem_stats_line + "\n"
    )

def build_data_json(points: int):
    # Determine available points
    available = readings_count if readings_count < buffer_size else buffer_size
    n = points if points < available else available
    if n <= 0:
        return json.dumps({"t": [], "h": []})
    # Collect from circular buffer ending at current_index-1
    out_t = []
    out_h = []
    start = (current_index - n) % buffer_size
    for i in range(n):
        idx = (start + i) % buffer_size
        out_t.append(tempList[idx])
        out_h.append(humList[idx])
    return json.dumps({"t": out_t, "h": out_h})

def build_html_page():
    # Maximum whole hours supported by POINTS_MAX
    hours_max = int(POINTS_MAX // 60)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Pico W DHT - Live</title>"
        "<link rel='stylesheet' href='https://unpkg.com/uplot@1.6.32/dist/uPlot.min.css'>"
        "<script src='https://unpkg.com/uplot@1.6.32/dist/uPlot.iife.min.js'></script>"
        "<style>body{font-family:system-ui,Segoe UI,Arial;margin:0;padding:12px;background:#0b0e12;color:#e6e8eb}"
        "#bar{display:flex;gap:8px;align-items:center;margin-bottom:8px}"
        "label{opacity:.8}input{width:6em}#chart{width:100%;height:240px;background:#11161d;border:1px solid #253041;border-radius:6px}"
        ".legend{font-size:12px;opacity:.8;margin-left:auto}span.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}"
        "</style></head><body>"
        "<div id='bar'>"
        "<label style='margin-left:8px'>Hours <input id='hours' type='number' min='1' max='" + str(hours_max) + "' step='1' value='1'></label>"
        "<label style='margin-left:8px'>Refresh ms <input id='refms' type='number' min='500' max='60000' step='500' value='2000'></label>"
        "<div class='legend'><span class='dot' style='background:#4fc3f7'></span>Temp <span class='dot' style='background:#81c784'></span>Hum</div>"
        "</div>"
        "<div id='chart'></div>"
        "<pre id='txt' style='opacity:.7'></pre>"
        "<pre style='opacity:.7'>" + str(mem_stats_line) + "</pre>"
        "<script>(function(){\n"
        "const el=document.getElementById('chart');\n"
        "const txt=document.getElementById('txt');\n"
        "const hoursEl=document.getElementById('hours');\n"
        "const refEl=document.getElementById('refms');\n"
        "let fetchPts=(function(){let hv=parseInt(hoursEl.value)||1;hv=Math.max(1,Math.min(" + str(hours_max) + ",hv));return Math.max(10,Math.min(" + str(POINTS_MAX) + ",hv*60));})();\n"
        "const initW = (el && el.clientWidth) ? el.clientWidth : 320;\n"
        "const opts = {\n"
        "  width: initW,\n"
        "  height: 240,\n"
        "  legend: {show: true},\n"
        "  series: [\n"
        "    {},\n"
        "    { label: 'Temp (°C)', stroke: '#4fc3f7', width: 2 },\n"
        "    { label: 'Hum (%)',  stroke: '#81c784', width: 2 }\n"
        "  ],\n"
        "  axes: [\n"
        "    { stroke:'#8aa1b4', grid:{ stroke:'#1e2734' } },\n"
        "    { stroke:'#8aa1b4', grid:{ stroke:'#1e2734' } },\n"
        "  ],\n"
        "  scales: { x: { time: true }, y: {} }\n"
        "};\n"
        "let u = new uPlot(opts, [[],[],[]], el);\n"
        "function draw(data) {\n"
        "  if (!data || !data.t || !data.t.length) return;\n"
        "  const nowSec = Math.floor(Date.now()/1000);\n"
        "  const n = data.t.length;\n"
        "  const times = new Array(n);\n"
        "  for (let i=0;i<n;i++) times[i] = nowSec - ((n - 1 - i) * 60);\n"
        "  u.setData([times, data.t, data.h]);\n"
        "  const all = data.t.concat(data.h);\n"
        "  let mn = all[0], mx = all[0];\n"
        "  for (let i=1;i<all.length;i++){ const v=all[i]; if(v<mn) mn=v; if(v>mx) mx=v; }\n"
        "  txt.textContent = 'min:'+mn+' max:'+mx+' last T:'+data.t[n-1]+' H:'+data.h[n-1]+' | hrs:'+((fetchPts/60).toFixed(1));\n"
        "}\n"
        "async function tick(){try{const r=await fetch('/data?points='+fetchPts,{cache:'no-store'});const d=await r.json();draw(d)}catch(e){ /* ignore */ }}\n"
        "function clampHours(){let hv=parseInt(hoursEl.value)||1;hv=Math.max(1,Math.min(" + str(hours_max) + ",hv));hoursEl.value=hv;fetchPts=Math.max(10,Math.min(" + str(POINTS_MAX) + ",hv*60));}\n"
        "function clampRef(){let rv=parseInt(refEl.value)||2000;rv=Math.max(500,Math.min(60000,rv));refEl.value=rv;return rv;}\n"
        "let _timer=null; function applyInterval(){const rv=clampRef(); if(_timer){clearInterval(_timer);} _timer=setInterval(tick,rv);}\n"
        "hoursEl.addEventListener('change',()=>{clampHours();tick()});\n"
        "refEl.addEventListener('change',()=>{applyInterval();tick()});\n"
        "window.addEventListener('resize',()=>{ try{ u.setSize({width:(el.clientWidth||320), height:240}); }catch(e){} });\n"
        "applyInterval(); tick();\n"
        "})();</script></body></html>"
    )

while True:
  try:
    # IMPORTANT TO BE 1
    sleep(1)
    # Increment all count variables
    sleepCount += 1
    displayMoveCount += 1
    # Memory: update low-water and log every ~30s
    mem_update_and_maybe_log(sleepCount)

    sensor.measure()
    temp = sensor.temperature()
    hum = sensor.humidity()

    # Accumulate readings for a minute-average entry
    minute_temp_sum += temp
    minute_hum_sum += hum
    minute_sample_count += 1

    if minute_sample_count >= SAMPLES_PER_MINUTE:
        avg_temp_minute = round(minute_temp_sum / minute_sample_count, 1)
        avg_hum_minute = round(minute_hum_sum / minute_sample_count, 1)
        tempList[current_index] = avg_temp_minute
        humList[current_index] = avg_hum_minute
        readings_count += 1
        current_index = (current_index + 1) % buffer_size
        minute_temp_sum = 0.0
        minute_hum_sum = 0.0
        minute_sample_count = 0
    
    # LED logic
    if temp < 40:
        #too low
        led_off()
        bled.high()
    elif temp >= 40 and temp < 45:
        #40 - 45
        led_off()
        bled.high()
        gled.high()
    elif temp >= 45 and temp < 50:
        #45 - 50
        led_off()
        gled.high()
    elif temp >= 50 and temp < 55:
        # 50 - 55
        led_off()
        gled.high()
        rled.high()
    elif temp >= 55:
        # too high
        led_off()
        rled.high()
    
    # Average readings
    avgTemp = (avgTemp + temp ) / 2
    avgHum = (avgHum + hum) / 2
    
    # Every JITTER_SECONDS change the location
    if displayMoveCount >= JITTER_SECONDS:
        displayMoveCount = 0
        # Oled draw in different spots
        randomX = random.randint(0, JITTER_X_MAX)
        randomY = random.randint(0, JITTER_Y_MAX)
    
    # Update the historical readings using circular buffer math (minute offsets)
    if readings_count >= MINUTES_5:
        index_5m = (current_index - MINUTES_5) % buffer_size
        temp5m = tempList[index_5m]
        hum5m = humList[index_5m]
    if readings_count >= MINUTES_10:
        index_10m = (current_index - MINUTES_10) % buffer_size
        temp10m = tempList[index_10m]
        hum10m = humList[index_10m]
    if readings_count >= MINUTES_30:
        index_30m = (current_index - MINUTES_30) % buffer_size
        temp30m = tempList[index_30m]
        hum30m = humList[index_30m]
    if readings_count >= MINUTES_60:
        index_60m = (current_index - MINUTES_60) % buffer_size
        temp60m = tempList[index_60m]
        hum60m = humList[index_60m]

    if sleepCount >= SECONDS_60M:
        # Reset sleep count periodically to keep values bounded
        sleepCount = 0

    t5_str = _hist_str(temp5m)
    t10_str = _hist_str(temp10m)
    t30_str = _hist_str(temp30m)
    t60_str = _hist_str(temp60m)
    h5_str = _hist_str(hum5m)
    h10_str = _hist_str(hum10m)
    h30_str = _hist_str(hum30m)
    h60_str = _hist_str(hum60m)
    # OLED power cycle control: toggle every 15s
    oled_phase_count += 1
    if oled_on:
        if oled_phase_count >= OLED_ON_SECONDS:
            oled.poweroff()
            oled_on = False
            oled_phase_count = 0
    else:
        if oled_phase_count >= OLED_OFF_SECONDS:
            oled.poweron()
            oled_on = True
            oled_phase_count = 0

    if not oled_on:
        # Skip drawing work while panel is off
        pass
    else:
        oled.contrast(1)
        oled.fill(0)
#     oled.text(f"{temp:.0f} {avgTemp:>2.0f} {temp5m:>2.0f} {temp10m:>2.0f} {temp30m:>2.0f}", randomX, randomY)   
#     oled.text(f"{hum:>2} {avgHum:>2.0f} {hum5m:>2.0f} {hum10m:>2.0f} {hum30m:>2.0f}", randomX, randomY+10)
    if oled_on:
        oled.text(f"{temp:.0f} {t5_str} {t10_str} {t30_str} {t60_str}", randomX, randomY)   
        oled.text(f"{hum:>2} {h5_str} {h10_str} {h30_str} {h60_str}", randomX, randomY+10)
        # Show last IP octet when connected
        if wlan and wlan.isconnected():
            try:
                ip0 = wlan.ifconfig()[0]
                if ip0:
                    dot = ip0.rfind('.')
                    last_oct = ip0[dot+1:] if dot >= 0 else ip0
                    oled.text(last_oct, randomX, randomY+20)
            except Exception:
                pass
        oled.show()

    # Handle one HTTP request per loop if server is running
    if server_sock:
        http_poll_and_respond(
            lambda: build_status_text(temp, hum),
            build_data_json,
            build_html_page,
        )
    else:
        # If WiFi creds exist but server is not up, try to (re)connect occasionally
        if WIFI_SSID and WIFI_PASSWORD:
            # Every ~30s, attempt to bring WiFi/server back
            if sleepCount % 30 == 0:
                if not wlan or not wlan.isconnected():
                    wlan = connect_wifi(WIFI_SSID, WIFI_PASSWORD)
                if wlan and wlan.isconnected():
                    start_http_server()
    
#     print(f"T: {temp}c {avgTemp:.0f} {temp5m:.0f} {temp10m:.0f} {temp30m:.0f} {temp60m:.0f}")
#     print(f"H: {hum}% {avgHum:.0f} {hum5m:.0f} {hum10m:.0f} {hum30m:.0f} {hum60m:.0f}")
    print(
        f"T: {temp}c {avgTemp:.0f} {t5_str} {t10_str} {t30_str} {t60_str}\n"
        f"H: {hum}% {avgHum:.0f} {h5_str} {h10_str} {h30_str} {h60_str}"
    )
    # print(f"H: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}")
    # Memory line is logged periodically by mem_update_and_maybe_log()
    # print(tempList)
    # print(humList)
        
  except OSError as e:
    print(e)
