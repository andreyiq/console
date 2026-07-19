# AGENTS.md — Console on MangoPi F133-A

## Что это
Обучающий bare-metal Rust-проект для MangoPi (Allwinner D1s/F133-A, RISC-V C906).
Цель: научиться выводить изображение на TFT ILI9488 по SPI, в итоге — NES-эмулятор на этом дисплее.
Ассистент выступает как учитель: объясняет по этапам (GPIO → CCU → SPI → ILI9488 → пиксели → картинка → DMA → NES), только то, что нужно для проекта.

## Железо
- Плата: MangoPi MQ (F133-A / D1s, RISC-V ~1 GHz, 64 MB DDR)
- Дисплей: 3.5" IPS ILI9488, 40-pin модуль, SPI режим (4-wire + D/C)
- Загрузка: FEL по USB через `xfel` → `ddr f133` → `write 0x40000000 bin` → `exec`
- Логический анализатор подключён к PC2 (SPI0-CLK)

### Распиновка SPI0
| F133 | Функция | Назначение | ILI9488 |
|---|---|---|---|
| PC2 | SPI0-CLK (func 2) / GPIO | CLK | LCD_SCL pin 11 |
| PC3 | SPI0-CS0 (func 2) / GPIO | CS | LCD_CS pin 9 |
| PC4 | SPI0-MOSI (func 2) | MOSI | LCD_SDA pin 13 |
| PC5 | SPI0-MISO (func 2) | MISO | LCD_SDO pin 14 |
| PE0 | GPIO out | D/C (0=cmd, 1=data) | LCD_RS pin 10 |
| PE1 | GPIO out | Reset | LCD_RST pin 15 |
| PE2 | UART0-TX (func 6) | Отладка | — |
| PE3 | UART0-RX (func 6) | — | — |

## Документация
- `docs/mangopi/f133_user_manual_v1.0.txt` — основной референс (CCU/PIO/SPI/DMA)
- `docs/mangopi/f133_datasheet_v1.2.pdf` — datasheet
- `docs/display/ili9488/` — datasheet, схема, init-код
- `docs/display/ili9488/BOE3.5IPS-ILI9488.TXT` — init-последовательность
- `docs/display/ili9488/invert the colors.txt` — 0x21 для IPS-инверсии
- `docs/display/ili9488/VCOM--uniformity of glow and burnout.txt` — 0xC5 с 0x4D (VCOM = -0.79688V)
- `docs/Allwinner-SoC/Allwinner D1s-F133 RISC-V/` — PDF'ы по SoC
- `docs/links.txt` — ссылки

## Книга теории
- `books/01-gpio.md` — Глава 1: GPIO (CFG/DAT/DRV/PULL, enum API, мигание PC2)
- `books/02-ccu.md` — Глава 2: CCU (запланировано)
- `books/03-spi.md` — Глава 3: SPI (запланировано)
- `books/04-ili9488.md` — Глава 4: ILI9488 (запланировано)

## Структура проекта (целевая после чистки)
```
src/console/
├── Cargo.toml          # без runes
├── memory.x            # RAM @ 0x40000000, 64M
├── .cargo/config.toml  # target riscv64gc-unknown-none-elf
├── run.sh              # xfel build/write/exec
├── archive/            # старый код (не удалять)
│   ├── nes.rs display.rs dma.rs ili9844.rs main.rs.old
│   └── roms/
└── src/
    ├── main.rs         # минимальный старт
    ├── uart.rs         # UART0 для отладки
    ├── utils.rs        # delay + println
    └── ccu.rs          # регистры CCU (расширяем по надобности)
```

## Дорожная карта
- [ ] Этап 0. Чистый старт: main + UART печатает "hello"
- [ ] Этап 1. GPIO: CFG/DAT/DRV/PULL. Мигаем PC2 (видно на анализаторе)
- [ ] Этап 2. CCU: тактирование и сброс периферии (включить SPI0)
- [ ] Этап 3. SPI: GCR/TCR/FCR/MBC/MTC/BCC. Отправка байта (анализатор)
- [ ] Этап 4. ILI9488: init, D/C, заливка цветом (0x21, 0xC5/0x4D)
- [ ] Этап 5. Окно и пиксели: RGB565, set_window, MemoryWrite
- [ ] Этап 6. Картинка: PNG → RGB565 массив, статичная картинка
- [ ] Этап 7. DMA (опц.): port 22 (SPI0-TX), пересылка кадра
- [ ] Этап 8. NES: вернуть runes, вывод кадра на ILI9488

## Текущий этап
Этап 0 → переход к Этапу 1 (GPIO на PC2).

## Конвенции
- `no_std`, `no_main`, `riscv_rt::entry`
- Регистры только через `read_volatile`/`write_volatile`
- Задержки — `utils::delay` (spin-loop)
- Отладка — `println!` в UART0 (115200 8N1)
- Прошивка: `./run.sh` (перезапуск платы перед каждым запуском)
