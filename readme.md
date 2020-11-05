# Laserscanner
Implementation of a laserscanner on a FPGA. In a high-speed polygon scanner system, the laser is deflected by a rotating prism or reflective mirror. 
The position of the laser is determined via a sensor such as a photodiode.
<br>
<img src="https://cdn.hackaday.io/images/7106161566426847098.jpg" align="center" height="300"/> 
<br>
Code is tested on the system shown in the image above, branded as [Hexastorm](https://www.hexastorm.com). 
The bill of materials (BOM) and links to FreeCad and PCB designs can be found on [Hackaday](https://hackaday.io/project/21933-open-hardware-fast-high-resolution-laser).
The code took most inspiration from [LDGraphy](https://github.com/hzeller/ldgraphy).

## Install Notes
Install [litex](https://github.com/enjoy-digital/litex), make a special folder for this installation.
For setting the power to laser over ic, you need to install smbus2. <br>
Python packages can be installed from apio to enable the toolchain. Apio nextpnr currently comes without python support.
[yowasp](http://yowasp.org/) comes with python support but only works on a X86 system. Yowasp takes a lot of time to run the first time.
The FPGA toolchain can be build from source via [ICE40](http://www.clifford.at/icestorm/). 

## Parameters
The following parameters describe the system. <br>
| parameter | description |
|---|---|
| RPM | revolutions per minute of the rotor |
| Start% | fraction of period where scanline starts |
| End% | fraction of period where scanline stops |
| SPINUP_TIME | seconds system waits for the rotor to stabilize speed |
| STABLE_TIME | seconds system tries to determine position laser with photodiode |
| FACETS | number of polygon facets|
| DIRECTION | exposure direction, i.e. forward or backward |
| SINGLE_LINE | system exposes fixed pattern, i.e. line|
| SINGLE_FACET | only one of the facets is used|
<br>
Using the above, the code determines the number of bits in a scanline. Via a serial port interface the user can push data to the scanner.
A line is preceded with a command which can be SCAN or STOP. The data is stored on the chip in block ram. 
If turned on the scanner reads out this memory via First In First Out (FIFO).

## Commands
| command | reply |
|---|---|
| STATUS | retrieve the error state and state of the laser scanner. This is the default reply of each command.|
| START | enable the scanhead |
| STOP | stop the scanhead |
| MOTORTEST | enable the motor |
| LASERTEST | turn on the laser|
| LINETEST | turn on the laser and the motor|
| PHOTODIODETEST | turn on motor, laser and turn off if photodiode is triggered|
| WRITE_L | next byte will be stored in memory |
| READ_D | retrieve debug information, not used |
<br>

## Detailed description
Look at the test folders and individually tests on how to use the code. The whole scanhead can be simulated virtually. 
As such, a scanner is not needed.

## Limitations
Only one of the 32 block rams is used. <br>
The laser can be pulsed at 50 MHZ but data can only be streamed to the laser scanner at 25 megabits per second. <br>
Parameters can not be changed on the fly. The binary has to be recompiled and uploaded to the scanhead. This is typically fast, i.e. seconds. <br>
The current implentation is targeted at a system with one laser bundle <br>
System is for writing to, i.e. exposing, a substrate. Reading should also be possible to enable optical coherence tomagraphy.<br>
System has no link for LIDAR measurements, circuit can be found [here](https://hackaday.io/project/163501-open-source-lidar-unruly).<br>
The FPGA controls all the stepper motors. At the moment it is not possible to use GCODE or apply acceleration profiles. <br>
<br>
Most of these implementation can be removed by improving the code. The current focus is on a proof of principle.
Next step would be to create a copy of [beagleg](https://github.com/hzeller/beagleg) with a FPGA code parser.
In a later stage, they might be merged.

## Other notes
### Migen examles
Examples used to gain experience with migen.

### I2C
In the current scanhead, I2C is used to set the power of the laser via a digipot.
I2C can be enabled on the Raspberry Pi as [follows](https://pimylifeup.com/raspberry-pi-i2c/).

```console
i2cdetect -y 1
```

This will produce output, here 28 is the address of the I2C device.
```console
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- -- 
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
20: -- -- -- -- -- -- -- -- 28 -- -- -- -- -- -- -- 
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
40: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- 
70: -- -- -- -- -- -- -- --
```
## Install Numpy
```console
sudo apt update
sudo apt remove python3-numpy
sudo apt install libatlas3-base
sudo pip3 install numpy
```
<!-- 
TODO:
  test current code base on scanner in single line and single facet mode;
    stopline
    scanline
  try to create a static pattern with the scanner in ring mode
  add movement, the head should determine wether it has to move after a line.
  There are others issues, but not needed for minimum viable product;
    constraint of multiple errors reduces space too much
    replace migen with nmigen
    varying chunksize
 -->