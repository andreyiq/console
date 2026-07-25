#!/bin/bash
# Сборка + прошивка + захват UART0 лога через /dev/ttyACM0.
# Требует: плата в FEL mode (зажать кнопку при reset, или подать на boot).
set -e
cd "$(dirname "$0")"

echo "=== build ==="
cargo build --release
rust-objcopy -O binary ./target/riscv64gc-unknown-none-elf/release/console \
  ./target/riscv64gc-unknown-none-elf/release/firmware.bin

# Захват UART запускаем ДО прошивки, иначе теряются сообщения загрузки
# (ddr-init, hello, init железа) — они успевают уйти пока стартует cat.
# На baseline ~1 FPS строка замера выходит раз в 10 секунд (LOG_EVERY=10
# в nes/mod.rs), поэтому 5 секунд не хватало ни на одну. Переопределить:
# UART_SECONDS=10 ./run_dma.sh
SECS="${UART_SECONDS:-45}"
LOG=./target/uart.log

# Ждём FEL ДО старта захвата, иначе окно захвата тратится на ожидание платы.
echo "=== ждём FEL (жми reset с зажатой кнопкой BOOT) ==="
for _ in $(seq 1 150); do
  xfel version >/dev/null 2>&1 && break
  sleep 1
done

echo "=== uart0 capture (${SECS}s) -> $LOG ==="
stty -F /dev/ttyACM0 115200 raw -echo
timeout "$SECS" cat /dev/ttyACM0 > "$LOG" &
CAT_PID=$!
sleep 1

echo "=== flash ==="
xfel ddr f133
xfel write 0x40000000 ./target/riscv64gc-unknown-none-elf/release/firmware.bin
xfel exec 0x40000000

echo "=== ждём ${SECS}s ==="
wait "$CAT_PID" || true
cat "$LOG"
