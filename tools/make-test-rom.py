"""Builds a tiny homebrew NDS ROM for smoke-testing the emulator.

Top screen is red; it turns green while the DS A button is held and blue while
the touchscreen is pressed. The top 16 pixel rows show a frame counter as 16
black/white blocks (bit 0 on the left), which makes emulation speed measurable.
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
  mov r7, #0
main:
  ldr r5, =0x04000006
wait_leave:
  ldrh r6, [r5]
  cmp r6, #192
  beq wait_leave
wait_vblank:
  ldrh r6, [r5]
  cmp r6, #192
  bne wait_vblank
  add r7, r7, #1
  ldr r3, =0x04000130
  ldrh r3, [r3]
  ldr r4, =0x027FF000
  ldr r4, [r4]
  ldr r1, =0x801F801F
  tst r3, #1
  ldreq r1, =0x83E083E0
  tst r4, #0x40
  ldreq r1, =0xFC00FC00
  ldr r0, =0x06802000
  ldr r2, =0x5800
fill:
  str r1, [r0], #4
  subs r2, r2, #1
  bne fill
  mov r8, #0
bitloop:
  mov r9, #1
  tst r7, r9, lsl r8
  ldrne r1, =0xFFFFFFFF
  ldreq r1, =0x80008000
  ldr r0, =0x06800000
  add r0, r0, r8, lsl #5
  mov r10, #16
rowloop:
  mov r11, #8
  mov r12, r0
pxloop:
  str r1, [r12], #4
  subs r11, r11, #1
  bne pxloop
  add r0, r0, #512
  subs r10, r10, #1
  bne rowloop
  add r8, r8, #1
  cmp r8, #16
  bne bitloop
  b main
"""
code9, _ = ks.asm(arm9, 0x02000000)
# ARM7 copies EXTKEYIN (bit 6 = pen down, active low) to main RAM for the ARM9.
arm7 = """
  ldr r0, =0x04000136
  ldr r1, =0x027FF000
loop:
  ldrh r2, [r0]
  str r2, [r1]
  b loop
"""
code7, _ = ks.asm(arm7, 0x037F8000)
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
