# Перед каждым запуском нужно перезапускать микросконтроллер т.к. при выполнении xfel exec управление переходит нашему кода и происходит выход из режима FEL
echo build
cargo build --release
# После сборки получается ELF файл, в котором кроме самого кода есть еще мета данные
echo obj
rust-objcopy -O binary ./target/riscv64gc-unknown-none-elf/release/console ./target/riscv64gc-unknown-none-elf/release/firmware.bin
# Получаем бинарный файл где нет мета данных только чистый код, так его можно залить по нужному адресу и передать туда управление, а если бы это был ELF то там бы оказалось начало файл т.е. .ELF
echo write
# Инициализация ddr
xfel ddr f133
echo write
# Записываем код по нужному адресу
xfel write 0x40000000 ./target/riscv64gc-unknown-none-elf/release/firmware.bin
echo exec
# Запускаем
xfel exec 0x40000000
