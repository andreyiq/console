#!/bin/bash
# Сборка + прошивка + захват UART0 лога через /dev/ttyACM0.
# Требует: плата в FEL mode (зажать кнопку при reset, или подать на boot).
set -e
cd "$(dirname "$0")"

echo "=== build ==="
cargo build --release
rust-objcopy -O binary ./target/riscv64gc-unknown-none-elf/release/console \
  ./target/riscv64gc-unknown-none-elf/release/firmware.bin

echo "=== flash ==="
xfel ddr f133
xfel write 0x40000000 ./target/riscv64gc-unknown-none-elf/release/firmware.bin
xfel exec 0x40000000

echo "=== uart0 log (5s) ==="
timeout 5 cat /dev/ttyACM0 || true
