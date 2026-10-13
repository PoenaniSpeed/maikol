"""Builds a small homebrew NDS ROM for testing the emulator.

Top screen, from the top, in 16-pixel rows of 16 black/white blocks (bit 0 left):
  row 0: frame counter (makes emulation speed measurable)
  row 1: raw touchscreen X from the touch controller (12 bit)
  row 2: raw touchscreen Y
  row 3: green when a cartridge read of the marker at ROM offset 0x10000 succeeds,
         red when it returns something else (the read is repeated every frame, so
         this catches an emulator that loses access to the ROM file mid-game)
Below that the screen is red; green while A is held, blue while the touchscreen
is pressed.

Usage: pip install keystone-engine && python3 tools/make-test-rom.py test.nds
"""
import struct, sys
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM

MARKER_OFFSET = 0x10000
MARKER = 0xC0FFEE42
SHARED = 0x027FF000  # main RAM words written by the ARM7: +0 EXTKEYIN, +4 touch X, +8 touch Y

ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
arm9 = f"""
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
  ldr r4, ={SHARED}
  ldr r4, [r4]
  ldr r1, =0x801F801F
  tst r3, #1
  ldreq r1, =0x83E083E0
  tst r4, #0x40
  ldreq r1, =0xFC00FC00
  ldr r0, =0x06808000
  ldr r2, =0x4000
fill:
  str r1, [r0], #4
  subs r2, r2, #1
  bne fill

  mov r6, r7
  ldr r5, =0x06800000
  bl drawbits
  ldr r6, ={SHARED + 4}
  ldr r6, [r6]
  ldr r5, =0x06802000
  bl drawbits
  ldr r6, ={SHARED + 8}
  ldr r6, [r6]
  ldr r5, =0x06804000
  bl drawbits

  bl cardread
  ldr r2, ={MARKER}
  cmp r0, r2
  ldreq r1, =0x83E083E0
  ldrne r1, =0x801F801F
  ldr r0, =0x06806000
  ldr r2, =0x800
cardfill:
  str r1, [r0], #4
  subs r2, r2, #1
  bne cardfill
  b main

@ r6 = value, r5 = address of the first pixel of the row
drawbits:
  mov r8, #0
bitloop:
  mov r9, #1
  tst r6, r9, lsl r8
  ldrne r1, =0xFFFFFFFF
  ldreq r1, =0x80008000
  add r0, r5, r8, lsl #5
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
  bx lr

@ Reads 0x200 bytes at ROM offset {MARKER_OFFSET:#x} with command B7; r0 = first word.
cardread:
  ldr r1, =0x04000204
  ldrh r2, [r1]
  bic r2, r2, #0x800
  strh r2, [r1]
  ldr r1, =0x040001A1
  mov r2, #0x80
  strb r2, [r1]
  ldr r1, =0x040001A8
  mov r2, #0xB7
  strb r2, [r1]
  mov r2, #{(MARKER_OFFSET >> 24) & 0xFF}
  strb r2, [r1, #1]
  mov r2, #{(MARKER_OFFSET >> 16) & 0xFF}
  strb r2, [r1, #2]
  mov r2, #{(MARKER_OFFSET >> 8) & 0xFF}
  strb r2, [r1, #3]
  mov r2, #{MARKER_OFFSET & 0xFF}
  strb r2, [r1, #4]
  mov r2, #0
  strb r2, [r1, #5]
  strb r2, [r1, #6]
  strb r2, [r1, #7]
  ldr r1, =0x040001A4
  ldr r2, =0xA1000000
  str r2, [r1]
  mov r0, #0
  mov r4, #0
  ldr r3, =0x04100010
read_wait:
  ldr r2, [r1]
  tst r2, #0x80000000
  beq read_done
  tst r2, #0x00800000
  beq read_wait
  ldr r2, [r3]
  cmp r4, #0
  moveq r0, r2
  add r4, r4, #1
  b read_wait
read_done:
  bx lr
"""

# ARM7: copies EXTKEYIN (bit 6 = pen down, active low) and the touch controller's raw
# X/Y readings to main RAM for the ARM9.
arm7 = f"""
  ldr r4, ={SHARED}
loop:
  ldr r0, =0x04000136
  ldrh r2, [r0]
  str r2, [r4]
  mov r0, #0xD0
  bl tsc
  str r0, [r4, #4]
  mov r0, #0x90
  bl tsc
  str r0, [r4, #8]
  b loop

@ r0 = touch controller command, returns the 12-bit result in r0
tsc:
  ldr r1, =0x040001C0
w1:
  ldrh r3, [r1]
  tst r3, #0x80
  bne w1
  ldr r3, =0x8A01
  strh r3, [r1]
  strh r0, [r1, #2]
w2:
  ldrh r3, [r1]
  tst r3, #0x80
  bne w2
  mov r3, #0
  strh r3, [r1, #2]
w3:
  ldrh r3, [r1]
  tst r3, #0x80
  bne w3
  ldrh r2, [r1, #2]
  ldr r3, =0x8201
  strh r3, [r1]
  mov r3, #0
  strh r3, [r1, #2]
w4:
  ldrh r3, [r1]
  tst r3, #0x80
  bne w4
  ldrh r3, [r1, #2]
  and r2, r2, #0x7F
  mov r0, r2, lsl #5
  orr r0, r0, r3, lsr #3
  bx lr
"""

code9 = bytes(ks.asm(arm9, 0x02000000)[0])
code7 = bytes(ks.asm(arm7, 0x037F8000)[0])
def pad(b, n=0x200): return b + b"\0" * ((-len(b)) % n)
code9, code7 = pad(code9), pad(code7)
o9 = 0x200
o7 = o9 + len(code9)
assert o7 + len(code7) <= MARKER_OFFSET
size = MARKER_OFFSET + 0x10000

h = bytearray(0x200)
h[0:12] = b"MAIKOLTEST\0\0"; h[12:16] = b"####"; h[16:18] = b"00"
struct.pack_into("<IIII", h, 0x20, o9, 0x02000000, 0x02000000, len(code9))
struct.pack_into("<IIII", h, 0x30, o7, 0x037F8000, 0x037F8000, len(code7))
struct.pack_into("<I", h, 0x80, size)
struct.pack_into("<I", h, 0x84, 0x4000)
def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc
struct.pack_into("<H", h, 0x15E, crc16(h[:0x15E]))

rom = bytearray(size)
rom[0:0x200] = h
rom[o9:o9 + len(code9)] = code9
rom[o7:o7 + len(code7)] = code7
struct.pack_into("<I", rom, MARKER_OFFSET, MARKER)
open(sys.argv[1], "wb").write(rom)
