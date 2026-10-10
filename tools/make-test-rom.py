"""Builds a tiny homebrew NDS ROM for smoke-testing the emulator.

Top screen is red; it turns green while the DS A button is held.
Usage: pip install keystone-engine && python3 tools/make-test-rom.py test.nds
"""
import struct, sys
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM
ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
arm9 = """
  ldr r0, =0x04000304
  ldr r1, =0x8003
  str r1, [r0]
  ldr r0, =0x04000000
  ldr r1, =0x00020000
  str r1, [r0]
  ldr r0, =0x04000240
  mov r1, #0x80
  strb r1, [r0]
main:
  ldr r3, =0x04000130
  ldrh r3, [r3]
  ldr r1, =0x801F801F
  tst r3, #1
  ldreq r1, =0x83E083E0
  ldr r0, =0x06800000
  ldr r2, =0x6000
fill:
  str r1, [r0], #4
  subs r2, r2, #1
  bne fill
  b main
"""
code9, _ = ks.asm(arm9, 0x02000000)
code7, _ = ks.asm("loop: b loop", 0x037F8000)
code9 = bytes(code9); code7 = bytes(code7)
def pad(b, n=0x200): return b + b"\0" * ((-len(b)) % n)
code9, code7 = pad(code9), pad(code7)
h = bytearray(0x200)
h[0:12] = b"MAIKOLTEST\0\0"; h[12:16] = b"####"; h[16:18] = b"00"
o9 = 0x200; o7 = o9 + len(code9)
struct.pack_into("<IIII", h, 0x20, o9, 0x02000000, 0x02000000, len(code9))
struct.pack_into("<IIII", h, 0x30, o7, 0x037F8000, 0x037F8000, len(code7))
struct.pack_into("<I", h, 0x80, o7 + len(code7))
struct.pack_into("<I", h, 0x84, 0x4000)
def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
struct.pack_into("<H", h, 0x15E, crc16(h[:0x15E]))
open(sys.argv[1], "wb").write(bytes(h) + code9 + code7)
