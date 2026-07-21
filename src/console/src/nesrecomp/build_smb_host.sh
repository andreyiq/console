#!/bin/bash
# build_smb_host.sh — сборка nesrecomp runner с оригинальным SMB generated code.
# super-mario-bros_full.c #include'ит bank00 и bank01, поэтому компилируем только его.
set -e
cd "$(dirname "$0")"

CC=gcc
CFLAGS="-O2 -Wall -Wno-unused -Wno-unused-function -I. -I./smb_generated -DHOST_DEBUG"

echo "Compiling runner..."
$CC $CFLAGS -c bare_runner.c -o bare_runner_host.o
$CC $CFLAGS -c mapper.c -o mapper_host.o
$CC $CFLAGS -c ppu.c -o ppu_host.o
$CC $CFLAGS -c stubs.c -o stubs_host.o
$CC $CFLAGS -c interp.c -o interp_host.o
$CC $CFLAGS -c mario_rom.c -o mario_rom_host.o

echo "Compiling SMB generated code (full.c includes bank00+bank01, ~5 min)..."
$CC $CFLAGS -c smb_generated/super-mario-bros_full.c -o smb_full_host.o
$CC $CFLAGS -c smb_generated/super-mario-bros_dispatch.c -o smb_dispatch_host.o

echo "Compiling host_main..."
$CC $CFLAGS -c host_main.c -o host_main.o

echo "Linking mario_host..."
$CC -O2 -o mario_host host_main.o bare_runner_host.o mapper_host.o ppu_host.o stubs_host.o interp_host.o mario_rom_host.o smb_full_host.o smb_dispatch_host.o -lm

echo "Done: ./mario_host [frames]"
