import os, time

hid = "/dev/emeet-pixy"
query = bytes.fromhex("09 05 00 04").ljust(32, b"\x00")

fd = os.open(hid, os.O_RDWR | os.O_NONBLOCK)
os.write(fd, query)
time.sleep(0.2)

try:
    data = os.read(fd, 64)
    print(data.hex(" "))
except BlockingIOError:
    print("no response")
finally:
    os.close(fd)
