# Complete project details at https://RandomNerdTutorials.com/raspberry-pi-pico-dht11-dht22-micropython/

from machine import Pin, I2C
from time import sleep, ticks_ms, ticks_diff
import machine
import network
try:
    import usocket as socket  # MicroPython
except ImportError:
    import socket  # Fallback
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

def http_poll_and_respond(response_text: str):
    """Try to accept a single connection and respond; non-blocking."""
    global server_sock
    if not server_sock:
        return
    try:
        conn, addr = server_sock.accept()
    except Exception:
        return  # nothing to accept right now
    try:
        conn.settimeout(0.5)
        _ = conn.recv(512)  # Read and ignore request
        hdr = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n\r\n"
        )
        conn.send(hdr)
        conn.send(response_text)
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

# Try WiFi + HTTP once at boot if credentials exist
wlan = None
if WIFI_SSID and WIFI_PASSWORD:
    wlan = connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if wlan and wlan.isconnected():
        start_http_server()
else:
    print("No WiFi credentials found in secrets.py; HTTP disabled.")

while True:
  try:
    # IMPORTANT TO BE 1
    sleep(1)
    # Increment all count variables
    sleepCount += 1
    displayMoveCount += 1

    sensor.measure()
    temp = sensor.temperature()
    hum = sensor.humidity()

    # Circular buffer update, for humidity and temperature readings
    tempList[current_index] = temp
    humList[current_index] = hum
    
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
    
    # Every 10s change the location
    if displayMoveCount > 30:
        displayMoveCount = 0
        # Oled draw in different spots
        randomX = random.randint(0, 10)
        randomY = random.randint(0, 45)
    
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

    # Prepare HTTP response text (same info as printed)
    http_text = (
        f"T: {temp}c {avgTemp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}\n"
        f"H: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}\n"
        f"Mem free: {gc.mem_free()/1024:.2f}KB {len(tempList)}\n"
    )

    # Handle one HTTP request per loop if server is running
    if server_sock:
        http_poll_and_respond(http_text)
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
    print(f"T: {temp}c {avgTemp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}")
    print(f"H: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}")
    print(f"Mem free: {gc.mem_free()/1024:.2f}KB {len(tempList)}")
    # print(tempList)
    # print(humList)
        
  except OSError as e:
    print(e)
