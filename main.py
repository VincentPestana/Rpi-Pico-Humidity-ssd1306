# Complete project details at https://RandomNerdTutorials.com/raspberry-pi-pico-dht11-dht22-micropython/

from machine import Pin, I2C
from time import sleep
import dht
from ssd1306 import SSD1306_I2C
import random
import gc

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
    
#     print(f"T: {temp}c {avgTemp:.0f} {temp5m:.0f} {temp10m:.0f} {temp30m:.0f} {temp60m:.0f}")
#     print(f"H: {hum}% {avgHum:.0f} {hum5m:.0f} {hum10m:.0f} {hum30m:.0f} {hum60m:.0f}")
    print(f"T: {temp}c {avgTemp:.0f} {temp5m} {temp10m} {temp30m} {temp60m}")
    print(f"H: {hum}% {avgHum:.0f} {hum5m} {hum10m} {hum30m} {hum60m}")
    print(f"Mem free: {gc.mem_free()/1024:.2f}KB {len(tempList)}")
    # print(tempList)
    # print(humList)
        
  except OSError as e:
    print(e)
