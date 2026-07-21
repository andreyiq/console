# nesrecomp — статическая рекомпиляция NES (Super Mario Bros.) в C

Этот каталог содержит bare-metal C runtime для [nesrecomp](https://github.com/...) —
инструмента статической рекомпиляции NES ROM в C код.

## Сборка libmario.a

```bash
./build_libmario.sh
```

Собирает `libmario.a` для RISC-V (rv64gc, lp64d, medany). Требует `riscv64-unknown-elf-gcc`.

Флаги оптимизации:
- `bare_runner.c`, `ppu.c`, `mapper.c`, `stubs.c`, `interp.c`, `mario_rom.c` → `-O2`
- `smb_generated/super-mario-bros_full.c`, `smb_generated/super-mario-bros_dispatch.c` → `-O0`

`-O0` для generated code — обходной путь бага `riscv64-unknown-elf-gcc`:
неправильно оптимизирует `switch(g_cpu.A)` в `func_8212_b0`.

## smb_generated/ — сгенерированный C код

Каталог `smb_generated/` НЕ в git (14MB, легко перегенерировать). Содержит:
- `super-mario-bros_full.c` — инклудит bank00 + bank01
- `super-mario-bros_full_bank00.c` — bank 0 (1MB)
- `super-mario-bros_full_bank01.c` — bank 1 (12MB)
- `super-mario-bros_dispatch.c` — dispatch таблица (274KB)

### Как получить smb_generated/ заново

1. Получить nesrecomp tool (см. основной репозиторий проекта).
2. Взять ROM `super-mario-bros.nes` (не входит в репозиторий — авторские права).
3. Запустить:
   ```bash
   nesrecomp super-mario-bros.nes --output-dir smb_generated/
   ```
4. Должны получиться 4 файла (см. список выше).

Без `smb_generated/` сборка `libmario.a` невозможна — `build_libmario.sh` завершится с ошибкой.

## Структура

- `bare_runner.c` — main runtime: g_cpu, g_ram, nes_read/nes_write, dispatch, vblank callback
- `ppu.c` — PPU registers + scanline renderer (с tile caching)
- `mapper.c` — NROM mapper
- `interp.c` — интерпретатор 6502 (fallback для dispatch miss)
- `stubs.c` — заглушки для неиспользуемых функций nesrecomp
- `mario_rom.c` — ROM данные (PRG + CHR), сгенерированные из .nes файла
- `host_main.c` — main для host-сборки (x86_64 Linux, отладка без bare-metal)
- `nes_runtime.h` — API nesrecomp (реализуем в bare_runner.c)
- `build_libmario.sh` — сборка для board (RISC-V bare-metal)
- `build_smb_host.sh` — сборка для host (x86_64, отладка)
- `build_smb_qemu.sh` — сборка для qemu-riscv64 (RISC-V Linux)

## Pipeline (двойная буферизация)

`nesrecomp_on_frame` в Rust использует pipeline: пока DMA передаёт кадр N,
ppu_render_frame рендерит кадр N+1 в другой буфер. Общее время кадра =
max(ppu, fl) вместо ppu + fl. См. `display.rs::flush_region_dma_start/finish`.

## Профилирование (mcycle)

`bare_runner.c` замеряет mcycle в `nes_vblank_callback`:
- `g_t0` — вход в NMI
- `g_t1` — после func_NMI()
- `g_t2` — после ppu_render_frame()
- `g_prev_t0` — старт прошлого NMI

Rust читает через getters `nesrecomp_get_t0/t1/t2/prev_t0` и логирует каждый кадр:
`f=N bt=.. nmi=.. ppu=.. fl=..`

## Результаты (MangoPi MQ, F133-A, ~1 ГГц)

| Версия | ppu | fl (DMA) | FPS |
|--------|-----|----------|-----|
| -O0 | 11.3 сек | 74 мс | 0.09 |
| -O2 | 922 мс | 74 мс | 1 |
| -O2 + tile cache | 82 мс | 74 мс | 7 |
| + pipeline | 77 мс | ~0 (параллельно) | 12 |
