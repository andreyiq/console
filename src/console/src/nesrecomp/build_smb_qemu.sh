#!/bin/bash
# build_smb_qemu.sh — сборка nesrecomp runner для RISC-V Linux (запуск через qemu-riscv64).
# Даёт RISC-V архитектуру как на board, но без прошивки — тест локально.
set -e
cd "$(dirname "$0")"

CC=riscv64-linux-gnu-gcc
CFLAGS="-O2 -Wall -Wno-unused -Wno-unused-function -I. -I./smb_generated -DHOST_DEBUG"

echo "Compiling runner (RISC-V Linux)..."
$CC $CFLAGS -c bare_runner.c -o bare_runner_qemu.o
$CC $CFLAGS -c mapper.c -o mapper_qemu.o
$CC $CFLAGS -c ppu.c -o ppu_qemu.o
$CC $CFLAGS -c stubs.c -o stubs_qemu.o
$CC $CFLAGS -c interp.c -o interp_qemu.o
$CC $CFLAGS -c mario_rom.c -o mario_rom_qemu.o

echo "Compiling SMB generated code (full.c, ~3 min)..."
$CC $CFLAGS -c smb_generated/super-mario-bros_full.c -o smb_full_qemu.o
$CC $CFLAGS -c smb_generated/super-mario-bros_dispatch.c -o smb_dispatch_qemu.o

echo "Compiling host_main..."
$CC $CFLAGS -c host_main.c -o host_main_qemu.o

echo "Linking mario_qemu..."
$CC -O2 -static -o mario_qemu host_main_qemu.o bare_runner_qemu.o mapper_qemu.o ppu_qemu.o stubs_qemu.o interp_qemu.o mario_rom_qemu.o smb_full_qemu.o smb_dispatch_qemu.o -lm

echo "Done: qemu-riscv64 ./mario_qemu [frames]"
