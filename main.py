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
POINTS_DEFAULT = 900
POINTS_MAX = 3600

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

# OLED burn-in mitigation: jitter settings
# Move the on-screen text every N seconds to spread pixel wear
JITTER_SECONDS = 10  # default ~10s; keep small to reduce static image time
JITTER_X_MAX = 10    # horizontal jitter range (pixels)
JITTER_Y_MAX = 45    # vertical jitter range (pixels; allow two 8px lines)

# Seconds converted from minutes
seconds5m = 5 * 60
seconds10m = 10 * 60
seconds30m = 30 * 60
seconds60m = 60 * 60

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
buffer_size = seconds60m + 1  # Size for 1 hour + current reading
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

# Track how many readings we have captured (for data endpoint)
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
        mem_stats_line = base + " | samples:{}/{}".format(filled, buffer_size)
        print(mem_stats_line)
        mem_last_report_sec = current_seconds

def build_status_text(t, h):
    # Include latest memory snapshot as a third line; if none yet, build one ad-hoc
    global mem_stats_line
    if not mem_stats_line:
        alloc = gc.mem_alloc()
        free = gc.mem_free()
        base = _format_mem_line(alloc, free, mem_min_free)
        filled = readings_count if readings_count < buffer_size else buffer_size
        mem_stats_line = base + " | samples:{}/{}".format(filled, buffer_size)
    return (
        f"T: {t}c {avgTemp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}\n"
        f"H: {h}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}\n"
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
    hours_max = int(POINTS_MAX // 3600)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Pico W DHT - Live</title>"
        "<style>body{font-family:system-ui,Segoe UI,Arial;margin:0;padding:12px;background:#0b0e12;color:#e6e8eb}"
        "#bar{display:flex;gap:8px;align-items:center;margin-bottom:8px}"
        "label{opacity:.8}input{width:6em}#chart{width:100%;height:240px;background:#11161d;border:1px solid #253041;border-radius:6px}"
        ".legend{font-size:12px;opacity:.8;margin-left:auto}span.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}"
        "</style></head><body>"
        "<div id='bar'>"
        "<label>Points <input id='points' type='number' min='30' max='" + str(POINTS_MAX) + "' step='10' value='" + str(POINTS_DEFAULT) + "'></label>"
        "<label style='margin-left:8px'>Decimate <input id='dec' type='checkbox' checked></label>"
        "<label style='margin-left:8px'>Hours <input id='hours' type='number' min='1' max='" + str(hours_max) + "' step='1' value='1'></label>"
        "<label style='margin-left:8px'>Refresh ms <input id='refms' type='number' min='500' max='60000' step='500' value='5000'></label>"
        "<div class='legend'><span class='dot' style='background:#4fc3f7'></span>Temp <span class='dot' style='background:#81c784'></span>Hum</div>"
        "</div>"
        "<canvas id='chart' width='1600' height='240'></canvas>"
        "<pre id='txt' style='opacity:.7'></pre>"
        "<pre style='opacity:.7'>" + str(mem_stats_line) + "</pre>"
        "<script>(function(){\n"
        "const cvs=document.getElementById('chart');\nconst ctx=cvs.getContext('2d');\n"
        "const txt=document.getElementById('txt');\nconst pointsEl=document.getElementById('points');\nconst decEl=document.getElementById('dec');\nconst hoursEl=document.getElementById('hours');\nconst refEl=document.getElementById('refms');\n"
        "let pts=parseInt(pointsEl.value)||" + str(POINTS_DEFAULT) + ";\n"
        "let fetchPts=(function(){let hv=parseInt(hoursEl.value)||1;hv=Math.max(1,Math.min(" + str(hours_max) + ",hv));return Math.max(10,Math.min(" + str(POINTS_MAX) + ",hv*3600));})();\n"
        "function scale(vals,min,max,size){const out=new Array(vals.length);const k=size/(max-min||1);for(let i=0;i<vals.length;i++)out[i]=(vals[i]-min)*k;return out}\n"
        "function bucketMinMax(vals, buckets){buckets=Math.max(1,Math.min(buckets,vals.length));const size=vals.length/buckets;const mins=new Array(buckets);const maxs=new Array(buckets);const avgs=new Array(buckets);for(let b=0;b<buckets;b++){let start=Math.floor(b*size), end=Math.floor((b+1)*size);if(b===buckets-1)end=vals.length;let mn=Infinity,mx=-Infinity,sum=0,c=0;for(let i=start;i<end;i++){const v=vals[i];if(v<mn)mn=v;if(v>mx)mx=v;sum+=v;c++}if(c===0){mn=mx=vals[Math.min(start,vals.length-1)];sum=mn;c=1}mins[b]=mn;maxs[b]=mx;avgs[b]=sum/c}return {mins,maxs,avgs}}\n"
        "function drawBand(xCount, minScaled, maxScaled, color){const w=cvs.width,h=cvs.height;const step=(w/(xCount-1||1));ctx.beginPath();for(let i=0;i<xCount;i++){const x=i*step, y=h-maxScaled[i];if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)}for(let i=xCount-1;i>=0;i--){const x=i*step, y=h-minScaled[i];ctx.lineTo(x,y)}ctx.closePath();ctx.fillStyle=color;ctx.globalAlpha=0.15;ctx.fill();ctx.globalAlpha=1}\n"
        "function plot(arr,color){const w=cvs.width,h=cvs.height;ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();for(let i=0;i<arr.length;i++){const x=i*(w/(arr.length-1||1));const y=h-arr[i];if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y)}ctx.stroke()}\n"
        "function draw(data){const w=cvs.width,h=cvs.height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#11161d';ctx.fillRect(0,0,w,h);\n"
        "// grid\nctx.strokeStyle='#1e2734';ctx.lineWidth=1;for(let i=0;i<=5;i++){const y=i*h/5;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}\n"
        "let t=data.t||[], u=data.h||[]; if(!t.length){return} const all=t.concat(u);\n"
        "let mn=Math.min.apply(null,all), mx=Math.max.apply(null,all); if(mn===mx){mn-=1;mx+=1}\n"
        "// Optional decimation to target Points with band (min-max)\n"
        "let tt=t, uu=u, tBand=null, uBand=null;\n"
        "if(decEl.checked){const buckets=Math.max(1,Math.min(pts,t.length));\n"
        "  if(t.length>buckets){const bt=bucketMinMax(t,buckets);tt=bt.avgs;tBand={mins:bt.mins,maxs:bt.maxs};}\n"
        "  if(u.length>buckets){const bu=bucketMinMax(u,buckets);uu=bu.avgs;uBand={mins:bu.mins,maxs:bu.maxs};}\n"
        "}\n"
        "// Recompute min/max for scaled drawing based on plotted arrays (not bands)\n"
        "const plotAll=tt.concat(uu); mn=Math.min(mn,Math.min.apply(null,plotAll)); mx=Math.max(mx,Math.max.apply(null,plotAll)); if(mn===mx){mn-=1;mx+=1}\n"
        "const st=scale(tt,mn,mx,h), su=scale(uu,mn,mx,h);\n"
        "// Bands\n"
        "if(tBand){const stmin=scale(tBand.mins,mn,mx,h), stmax=scale(tBand.maxs,mn,mx,h);drawBand(tBand.mins.length, stmin, stmax, '#4fc3f7');}\n"
        "if(uBand){const sumin=scale(uBand.mins,mn,mx,h), sumax=scale(uBand.maxs,mn,mx,h);drawBand(uBand.mins.length, sumin, sumax, '#81c784');}\n"
        "// Plot lines\nplot(st,'#4fc3f7'); plot(su,'#81c784');\n"
        "// Overall min/max overlays\nctx.strokeStyle='#394a5f';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(0,h-scale([mn],mn,mx,h)[0]);ctx.lineTo(w,h-scale([mn],mn,mx,h)[0]);ctx.stroke();ctx.beginPath();ctx.moveTo(0,h-scale([mx],mn,mx,h)[0]);ctx.lineTo(w,h-scale([mx],mn,mx,h)[0]);ctx.stroke();ctx.setLineDash([]);\n"
        "const rawN=t.length, plotN=tt.length; const decMsg=(decEl.checked&&plotN<rawN)?(rawN+'→'+plotN):'off';\n"
        "txt.textContent='min:'+mn+' max:'+mx+' last T:'+t[t.length-1]+' H:'+u[u.length-1]+' | hrs:'+((fetchPts/3600).toFixed(0))+' pts:'+pts+' dec:'+decMsg;}\n"
        "async function tick(){try{const r=await fetch('/data?points='+fetchPts,{cache:'no-store'});const d=await r.json();draw(d)}catch(e){ console.log(e)/* ignore */}}\n"
        "function clampPts(){let v=parseInt(pointsEl.value)||" + str(POINTS_DEFAULT) + ";v=Math.max(10,Math.min(" + str(POINTS_MAX) + ",v));pts=v;pointsEl.value=v;}\n"
        "function clampHours(){let hv=parseInt(hoursEl.value)||1;hv=Math.max(1,Math.min(" + str(hours_max) + ",hv));hoursEl.value=hv;fetchPts=Math.max(10,Math.min(" + str(POINTS_MAX) + ",hv*3600));}\n"
        "function clampRef(){let rv=parseInt(refEl.value)||5000;rv=Math.max(500,Math.min(60000,rv));refEl.value=rv;return rv;}\n"
        "let _timer=null; function applyInterval(){const rv=clampRef(); if(_timer){clearInterval(_timer);} _timer=setInterval(tick,rv);}\n"
        "pointsEl.addEventListener('change',()=>{clampPts();tick()});\n"
        "decEl.addEventListener('change',()=>{tick()});\n"
        "hoursEl.addEventListener('change',()=>{clampHours();tick()});\n"
        "refEl.addEventListener('change',()=>{applyInterval();tick()});\n"
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

    # Circular buffer update, for humidity and temperature readings
    tempList[current_index] = temp
    humList[current_index] = hum
    readings_count += 1
    
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
    
    # Update the historical readings using circular buffer math
    if sleepCount >= seconds5m:
        index_5m = (current_index - seconds5m) % buffer_size
        temp5m = tempList[index_5m]
        hum5m = humList[index_5m]
    if sleepCount >= seconds10m:
        index_10m = (current_index - seconds10m) % buffer_size
        temp10m = tempList[index_10m]
        hum10m = humList[index_10m]
    if sleepCount >= seconds30m:
        index_30m = (current_index - seconds30m) % buffer_size
        temp30m = tempList[index_30m]
        hum30m = humList[index_30m]
    if sleepCount >= seconds60m:
        index_60m = (current_index - seconds60m) % buffer_size
        temp60m = tempList[index_60m]
        hum60m = humList[index_60m]
        # Reset sleep count but don't pop list items anymore
        sleepCount = 0

    # Update circular buffer index
    current_index = (current_index + 1) % buffer_size

    oled.contrast(1)
    # Oled control
    #if sleepCount % 5 == 0:
#         oled.contrast(1)
     #   oled.poweron()
    #else:
#         oled.contrast(0)
        #oled.poweroff()
    
    oled.fill(0)
#     oled.text(f"{temp:.0f} {avgTemp:>2.0f} {temp5m:>2.0f} {temp10m:>2.0f} {temp30m:>2.0f}", randomX, randomY)   
#     oled.text(f"{hum:>2} {avgHum:>2.0f} {hum5m:>2.0f} {hum10m:>2.0f} {hum30m:>2.0f}", randomX, randomY+10)
    oled.text(f"{temp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}", randomX, randomY)   
    oled.text(f"{hum:>2} {hum5m} {hum10m} {hum30m} {hum60m}", randomX, randomY+10)
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
    print(f"T: {temp}c {avgTemp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}\nH: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}")
    # print(f"H: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}")
    # Memory line is logged periodically by mem_update_and_maybe_log()
    # print(tempList)
    # print(humList)
        
  except OSError as e:
    print(e)
