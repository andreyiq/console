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
- `books/02-ccu.md` — Глава 2: CCU (тактирование, reset, включение SPI0)
- `books/03-spi.md` — Глава 3: SPI (протокол, регистры SPI0, отправка байта)
- `books/04-ili9488.md` — Глава 4: ILI9488 (4-wire SPI, IM, init, MADCTL, RGB888, MemoryWrite)
- `books/05-dma.md` — Глава 5: DMAC (дескрипторы, DRQ-порты, SPI0-TX = 22, авто-гейтинг, TF_DRQ_EN)
- `books/06-nes.md` — Глава 6: NES (архитектура 2A03/2C02, iNES, мапперы, runes API, план интеграции)

## Структура проекта
```
src/console/
├── Cargo.toml          # runes = "0.2.5" (чистый, с crates.io)
├── memory.x            # RAM @ 0x40000000, 64M
├── .cargo/config.toml  # target riscv64gc-unknown-none-elf
├── run.sh              # xfel build/write/exec
├── run_dma.sh          # то же + захват UART-лога
├── archive/            # старый код + ROM'ы (не удалять, не в git)
│   ├── nes.rs display.rs dma.rs ili9844.rs main.rs.old
│   └── roms/           # mario.nes, nestest.nes, pac-man.nes
└── src/
    ├── main.rs         # init железа → nes::run()
    ├── uart.rs         # UART0 + UartWriter для println!
    ├── utils.rs        # delay + println! (core::fmt::Write)
    ├── ccu.rs          # регистры CCU (enum Peripheral, PLL_PERI, SPI0_CLK)
    ├── gpio.rs         # generic Pin + enum Port/Func/Pull
    ├── spi.rs          # enum Spi + SpiInfo + prepare_dma
    ├── dma.rs          # DMAC (Descriptor, Dma::Channel0, DRQ-порты)
    ├── display.rs      # ILI9488 (Display, flush_buffer_dma, flush_region_dma)
    ├── fb.rs           # framebuffer 480×320 RGB888 (рамка, заливка)
    └── nes/            # NES поверх крейта runes
        ├── mod.rs      # run() — сборка машины + главный цикл + замер FPS
        ├── cart.rs     # StaticCart (no_std, без Vec) + парсер iNES
        ├── mapper0.rs  # свой Mapper0 (NROM) без UB
        ├── palette.rs  # 64 цвета NES → RGB888
        ├── screen.rs   # FbScreen: ppu::Screen → NES_BUF 256×240 + оверлей FPS
        ├── input.rs    # NoInput (заглушка, кнопок нет)
        └── speaker.rs  # NoAudio (заглушка, звука нет)
```

## Дорожная карта
- [x] Этап 0. Чистый старт: main + UART печатает "hello"
- [x] Этап 1. GPIO: CFG/DAT/DRV/PULL. Мигаем PC2 (видно на анализаторе)
- [x] Этап 2. CCU: тактирование и сброс периферии (включить SPI0, PLL_PERI 600 М / 15 = 20 М SCLK)
- [x] Этап 3. SPI: GCR/TCR/FCR/MBC/MTC/BCC. Отправка байта (анализатор)
- [x] Этап 4. ILI9488: init, D/C, заливка цветом (RGB888, 0x21, 0xC5/0x4D)
- [x] Этап 5. Окно и пиксели: framebuffer 480×320 RGB888, draw_number, fill_rect, set_pixel
- [x] Этап 5.5. Framebuffer в RAM + один flush на кадр
- [x] Этап 6. DMA: DMAC channel 0, DRQ SPI0-TX=22, TF_DRQ_EN, авто-гейтинг, ~5 FPS
- [x] Этап 7. NES: runes на экране, кадр 256×240 по центру ILI9488, замер FPS
- [x] Этап 8. D-cache C906: эмуляция 724 → 27 мс/кадр, 1 → 9 FPS
- [x] Этап 9. Pipeline DMA: эмуляция ушла под передачу, 9 → 13 FPS
- [x] Этап 10. SPI SCLK 20 → 37.5 МГц: 13 → 25 FPS
- [ ] Этап 11. Дальше: либо SCLK 50 МГц, либо оптимизация эмуляции ← текущий

## Замеры (25.07.2026)

`mario.nes` (mapper 0), нативные 256×240 по центру 480×320.
`emu` — эмуляция runes (CPU 6502 + PPU + APU), `fl` — передача 184 320 байт
через DMA→SPI, `wait` — простой CPU в ожидании этой передачи.

| Версия | emu | fl / wait | FPS |
|---|---|---|---|
| baseline (D-cache выключен, SCLK 20 МГц) | 724 мс | fl 73 мс | 1 |
| + D-cache, branch prediction | 27 мс | fl 73 мс | 9 |
| + pipeline DMA | 26 мс | wait 46 мс | 13 |
| + SCLK 37.5 МГц | 26 мс | wait 12 мс | **25** |

**25× от начала.** Все три шага легли ровно в расчёт:

- pipeline: время кадра стало `max(emu, fl)` вместо суммы (26 + 46 ≈ 73 = fl)
- SCLK 37.5 МГц: 184320 × 8 / 37.5 МГц = 39.3 мс → 25.4 FPS, замерено 25
- `wait` = fl − emu = 39.3 − 26 ≈ 12 мс, сходится

Всё ещё боттлнек SPI (39 мс против 26 мс эмуляции), но запас сократился.

`fl=73 мс` совпадает с теорией (184320 × 8 / 20 МГц = 73.7 мс) — значит путь
DMA→SPI идёт на полной скорости железа, и это же подтверждает, что
`CPU_HZ = 1_008_000_000` в `nes/mod.rs` верна.

## Что было не так с кэшем (этап 8, сделано)

Читаем вендорские CSR до и после `cache::enable()`:

```
cache before: mhcr=0x109 mxstatus=0xc0408000 mhint=0x4000
cache after:  mhcr=0x17f mxstatus=0xc0608000 mhint=0x610c
```

`MHCR` до = `0x109` — биты 0 (IE, I-cache), 3 (WB), 8 (WBR). То есть
**I-cache был уже включён** (его поднимает ddr-payload самого xfel), а вот
**бит 1 (DE, D-cache) был сброшен — D-cache не работал**. Тормозили не
выборки инструкций, а доступы к данным: PPU и CPU 6502 гоняли каждое
чтение/запись прямо в DDR2-528.

После: `0x17f` — добавились DE (D-cache), WA (write allocate), RS (return
stack), BPE (branch prediction), BTB. Биты 7 и 12 из записанного `0x11ff`
на этом ядре не реализованы и читаются нулями — это нормально.

`MXSTATUS`: бит 22 (THEADISAEE, расширения T-Head) был уже выставлен,
добавили бит 21 (MM — аппаратная обработка невыровненных доступов).

Реализация — `src/cache.rs`. Когерентность с DMA сделана в `dma.rs::start()`:
перед запуском канала зовём `cache::clean_dcache()` (`dcache.call` + `sync.s`),
иначе DMAC прочитает из DDR устаревший дескриптор и прошлый кадр. Один
`dcache.call` чистит кэш целиком, поэтому покрывает и дескриптор, и буфер.

## Как сделан pipeline (этап 9, сделано)

`screen.rs` — два буфера кадра по 184 КБ. `FbScreen` держит указатель на тот,
куда PPU пишет сейчас. `render()` (PPU зовёт его на scanline 241, когда все
240 видимых строк уже в буфере) публикует законченный буфер в `READY_IDX` и
переключает запись на другой. Главный цикл в `mod.rs`: дождаться прошлого
переноса → нанести оверлей FPS на готовый буфер → запустить неблокирующий
`flush_region_dma_start` → вернуться к эмуляции.

Порядок важен: ждать прошлый DMA надо **до** того, как трогаем SPI, потому что
`start()` держит CS низким и сам шлёт команды — две передачи разом невозможны.

Запас по времени большой: между `render()` и ожиданием в главном цикле проходит
одна инструкция 6502, а PPU до первого пикселя нового кадра стоит весь vblank
(21 scanline ≈ 2400 такт CPU). Так что буфер под DMA точно никто не перезапишет.

Отдельно пришлось учесть, что `put()` вызывается только при включённом
рендеринге (`runes/ppu.rs:590`). Mario гасит экран на сменах уровня, и тогда за
кадр не приходит ни одного пикселя — при наивной двойной буферизации экран
мигал бы между двумя старыми кадрами. Поэтому в `FbScreen` есть флаг `dirty`:
нет пикселей → буфер не публикуем и не флашим совсем (заодно экономим 73 мс).

## Про разгон SPI (этап 10, сделано)

Такт задаётся в `ccu::set_spi0_clock_pllperi(m)`: модуль = 600 / m, дальше
контроллер делит на 2 (`spi.rs`, `CLK_CTL`: DRS=1 + CDR2=0). Сейчас `m = 8`
→ модуль 75 МГц → SCLK 37.5 МГц. Таблица значений — в докстринге функции.

**Это разгон.** Datasheet ILI9488 даёт минимум 50 нс на цикл записи, то есть
штатный предел — 20 МГц. На 37.5 МГц конкретный модуль работает, но это не
гарантия: отказ проявится не честной ошибкой, а битыми пикселями или сбитой
синхронизацией. Если появятся артефакты — первым делом вернуть `m = 15`.

## Этап 11: что осталось

Времена сблизились (fl 39 мс, emu 26 мс), поэтому дальше имеет смысл двигать
оба конца:

1. **SCLK 50 МГц** (`m = 6` → модуль 100 МГц): fl 29.5 мс →
   `max(26, 29.5)` ≈ 30 мс → **~34 FPS**. Разгон ×2.5 от datasheet.
2. **Эмуляция 26 мс** — после п.1 станет почти вплотную к боттлнеку:
   - `&mut dyn` в горячем пути runes: `ppu.rs` держит `scr: &mut dyn Screen`
     (виртуальный вызов на каждый пиксель), `RefMapper` — `&mut dyn Mapper`
     (виртуальный вызов на каждый доступ к памяти). Лечится дженериками.
   - `write_volatile` в `FbScreen::put` — 3 volatile-записи на пиксель мешают
     компилятору их объединять. Для обычной RAM volatile не нужен.
   - `opt-level = 2` → попробовать `3`.
3. Для 60 FPS (16.7 мс на кадр) нужно и то и другое: SCLK ~100 МГц и emu
   ниже 16 мс. На SPI это уже вряд ли достижимо — у F133 есть настоящий
   LCD/TCON с параллельным RGB-интерфейсом, но наш модуль подключён по SPI.

## Конвенции
- `no_std`, `no_main`, `riscv_rt::entry`
- Регистры только через `read_volatile`/`write_volatile`
- Задержки — `utils::delay` (spin-loop)
- Отладка — `println!` в UART0 (115200 8N1)
- Прошивка: `./run.sh` (перезапуск платы перед каждым запуском)
