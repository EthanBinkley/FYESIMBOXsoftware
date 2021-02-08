# Main file to run on Raspberry Pi to coordinate simbox operations.

#import sys
from datetime import datetime, timedelta
import multiprocessing.shared_memory as sm
#import socket

import numpy as np
import serial
import board
import RPi.GPIO as GPIO
from timeloop import Timeloop

from virtual_env import simulation
from flow_conversion import flow_to_bytes
from mass_spec import make_fake_ms
from uv_conversion import uv_conversion, make_fake_uv
from add_noise import fuzz


FREQUENCY = 20 #Hz
ERROR_STATE = 0 #Default is to keep this constant


# Define addresses
DAC = (0x28, 0x29) #DAC I2C addresses
# DAC0: pressure sensors & thermistors 1-4
# DAC1: mass spec, IR flow & thermistor 5
P = (0x0, 0x1, 0x2, 0x3) #Pressure sensor DAC channels
T = (0x4, 0x5, 0x6, 0x7, 0x4) #Thermistor DAC channels
MS = (0x0, 0x1) #Mass spec DAC channels
IR = (0x2, 0x3) #IR flow sensor DAC channels


# Define GPIO pins
GPIO_PINS = (4, 14, 15, 17, 18, 27) #not set in stone
RED, GRN = 21, 13


# Define (inverse) calibrations (units (which?) -> voltage)
pres_cals = (lambda x: 0.2698*x + 0.1013, lambda x: 0.2462*x + 0.4404,
             lambda x: 0.2602*x + 0.1049, lambda x: 0)
therm_cals = (lambda x: 0, lambda x: 0,
              lambda x: 0, lambda x: 0,
              lambda x: 0)


# Set up connections (and misc.)
i2c = board.I2C()
arduino = serial.Serial("/dev/ttyACM0", baudrate=115200, timeout=1)
arduino.flush()

sensor_mem = sm.SharedMemory(name="sensors", create=True, size=120) #Edit with correct size
valve_mem = sm.SharedMemory(name="valves", create=True, size=6)
sensor_data = np.ndarray(shape=(15,), dtype=np.float64, buffer=sensor_mem.buf)
valve_states = np.ndarray(shape=(6,), dtype=np.bool, buffer=valve_mem.buf)
valve_states[:] = [True, True, True, True, True, True] # Edit to appropriate starting states

GPIO.setup(RED, GPIO.OUT)
GPIO.setup(GRN, GPIO.OUT)
for pin in GPIO_PINS:
    GPIO.setup(pin, GPIO.IN)

start_t = 0
tl = Timeloop()


#Main looping function
@tl.job(interval=timedelta(seconds=1/FREQUENCY))
def run():
    global sensor_data, valve_states, start_t
    # Set start time
    if not start_t:
        GPIO.output(GRN, GPIO.HIGH)
        start_t = datetime.datetime.now()
    
    # Sensor data: (15 floats)
    # pres0, pres1, pres2, pres3, therm0, therm1, therm2, therm3, therm4,
    # dig_flow0, dig_flow1, dig_temp0, dig_temp1, ir_flow0, ir_flow1
    
    # Process data (only read from 'sensor_data'; mutate 'sensors')
    sensors = [fuzz(d) for d in sensor_data]
    for i in range(4):
        sensors[i] = pres_cals[i](sensors[i])
    for i in range(5):
        sensors[i + 4] = therm_cals[i](sensors[i + 4])
        
    # Make fake UV and mass spec. data
    uva, uvb, uvc1, uvc2, uvd = make_fake_uv()
    mass0, mass1 = make_fake_ms()
    
    #Prepare digital data to send to Arduino
    #9 bytes for each flow, 10 for UV, 1 for error state
    f0_data = flow_to_bytes(sensors[8], sensors[10])
    f1_data = flow_to_bytes(sensors[9], sensors[11])
    uv_data = uv_conversion(uva, uvb, uvc1, uvc2, uvd)
    digital_data = [*f0_data, *f1_data, *uv_data, ERROR_STATE]
    
    #Prepare analog data to send to DACs
    analog_data_0 = [P[0], sensors[0], P[1], sensors[1],
                     P[2], sensors[2], P[3], sensors[3],
                     T[0], sensors[4], T[1], sensors[5],
                     T[2], sensors[6], T[3], sensors[7]]
    analog_data_1 = [MS[0], mass0, MS[1], mass1,
                     IR[0], sensors[12], IR[1], sensors[13], 
                     T[4], sensors[8]]
    
    #Output data
    i2c.writeto(DAC[0], analog_data_0)
    i2c.writeto(DAC[1], analog_data_1)
    arduino.write(bytes(digital_data))
    
    #Valve feedback
    valve_states[:] = [GPIO.input(pin) for pin in GPIO_PINS]
    
    
if __name__ == "__main__":
    try:
        tl.start(block=True)
    finally:
        #Turn off LEDs
        GPIO.output(GRN, GPIO.LOW)
        GPIO.output(RED, GPIO.LOW)

        #Close shared memory
        sensor_mem.close()
        valve_mem.close()
        sensor_mem.unlink()
        sensor_mem.unlink()
