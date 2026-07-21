#!/bin/bash
# build_libmario.sh — сборка libmario.a (nesrecomp C runtime) для RISC-V target.
# Использует оригинальный SMB generated code (с inline dispatch).
#
# ВАЖНО: generated code (smb_full, smb_dispatch) собирается с -O0 из-за бага
# riscv64-unknown-elf-gcc (неправильно оптимизирует switch(g_cpu.A) в func_8212).
# Наш код (ppu, bare_runner, mapper, stubs, interp, mario_rom) — с -O2 для скорости.
# ppu_render_frame особенно чувствителен: 61440 пикселей × 5-10 reads = миллионы
# вызовов ppu_read_vram; при -O0 это 11 сек/кадр, при -O2 должно быть <100 мс.
set -e
cd "$(dirname "$0")"

CC=riscv64-unknown-elf-gcc
# Базовые флаги (общие для всех)
BASE_FLAGS="-march=rv64gc -mabi=lp64d -mcmodel=medany -fno-omit-frame-pointer -ffreestanding -nostdlib -Wall -Wno-unused -Wno-unused-function -I. -I./smb_generated"
# -O0 для generated code (баг компилятора на switch)
CFLAGS_O0="$BASE_FLAGS -O0"
# -O2 для нашего кода (ppu.c — критично!)
CFLAGS_O2="$BASE_FLAGS -O2"
AR=riscv64-unknown-elf-ar

echo "Compiling our code with -O2 (ppu.c is critical)..."
$CC $CFLAGS_O2 -c bare_runner.c -o bare_runner.o
$CC $CFLAGS_O2 -c mapper.c -o mapper.o
$CC $CFLAGS_O2 -c ppu.c -o ppu.o
$CC $CFLAGS_O2 -c stubs.c -o stubs.o
$CC $CFLAGS_O2 -c interp.c -o interp.o
$CC $CFLAGS_O2 -c mario_rom.c -o mario_rom.o

echo "Compiling SMB generated code with -O0 (~5 min, compiler bug workaround)..."
$CC $CFLAGS_O0 -c smb_generated/super-mario-bros_full.c -o smb_full.o
$CC $CFLAGS_O0 -c smb_generated/super-mario-bros_dispatch.c -o smb_dispatch.o

echo "Archiving libmario.a..."
rm -f libmario.a
$AR rcs libmario.a bare_runner.o mapper.o ppu.o stubs.o interp.o mario_rom.o smb_full.o smb_dispatch.o

echo "Done: libmario.a ($(stat -c %s libmario.a) bytes)"
