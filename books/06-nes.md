# Этап 7: NES-эмулятор на bare-metal Rust

## 1. Зачем и что мы строим

Цель — запустить настоящий NES-эмулятор на MangoPi MQ (F133-A) без ОС.
У нас уже есть:

- UART0 (отладка),
- CCU (тактирование),
- SPI0 на ~20 МГц,
- ILI9488 480×320 RGB888,
- framebuffer в DDR + flush через DMA.

Осталось добавить **CPU/PPU/APU-эмуляцию NES** и **вывод кадра 256×240 на наш экран**.
В качестве ядра эмулятора возьмём крейт [`runes`](https://crates.io/crates/runes) (v0.2.5) — `#![no_std]`, чистый Rust, поддерживает Mapper 1/2/4.

## 2. Аппаратура NES (краткая теория)

### 2.1 CPU — Ricoh 2A03

- Ядро **6502** (~1.79 МГц на NTSC, ~1.66 МГц на PAL).
- 8-битные регистры A, X, Y, SP, флаги, 16-битный PC.
- Адресное пространство 64 KB.
- Тот же 2A03 содержит **APU** (генераторы звука) и **DMA для OAM** (спрайтов).
- Прерывания: **NMI** (от PPU в VBlank), **IRQ** (от APU/картриджа), **RESET**.

### 2.2 PPU — Ricoh 2C02

- Рисует **256×240 пикселей** (NTSC; видимых обычно 256×224).
- Палитра **64 цвета** (из них 54 уникальных).
- Tile-based рендеринг: **8×8** пиксельные тайлы, 2 таблицы (left/right half).
- Background: 32×30 тайлов, scroll.
- Sprites: 64 штуки, 8×8 или 8×16, приоритеты.
- Сигналы: **VBlank** (NMI), **sprite 0 hit**, **overflow**.
- Кадр = 262 scanline'а, на каждом ~341 cycle.

### 2.3 APU

- 2 pulse-канала, 1 triangle, 1 noise, 1 DPCM (сэмплы).
- Частота дискретизации ~44 100 Гц (как в `runes::apu::AUDIO_SAMPLE_FREQ`).
- На железе F133 у нас пока нет аудиовыхода → `Speaker::queue` будет заглушкой.

### 2.4 Карта памяти CPU

| Диапазон | Размер | Назначение |
|---|---|---|
| `$0000–$07FF` | 2 KB | Internal RAM |
| `$0800–$1FFF` | — | Зеркала RAM (3 копии) |
| `$2000–$2007` | 8 B | PPU регистры |
| `$2008–$3FFF` | — | Зеркала PPU (каждые 8 байт) |
| `$4000–$4017` | 24 B | APU и I/O регистры |
| `$4018–$401F` | — | Тестовое (обычно отключено) |
| `$4020–$FFFF` | — | Картридж (PRG-ROM, SRAM, маппер) |

### 2.5 Карта памяти PPU

| Диапазон | Размер | Назначение |
|---|---|---|
| `$0000–$0FFF` | 4 KB | Pattern table 0 (тайлы) |
| `$1000–$1FFF` | 4 KB | Pattern table 1 |
| `$2000–$2FFF` | 4 KB | Nametable 0..3 (с учётом mirroring) |
| `$3000–$3EFF` | — | Зеркала nametable |
| `$3F00–$3F1F` | 32 B | Палитра (16 цветов × 2) |
| `$3F20–$3FFF` | — | Зеркала палитры |

## 3. iNES формат (`.nes`)

Заголовок — 16 байт:

```c
struct INesHeader {
  magic: [u8; 4],        // "NES\x1a"
  prg_rom_nbanks: u8,   // × 16 KB
  chr_rom_nbanks: u8,   // × 8 KB
  flags6: u8,           // mapper low nibble + mirroring + trainer
  flags7: u8,           // mapper high nibble
  prg_ram_nbanks: u8,   // × 8 KB
  flags9: u8,
  flags10: u8,
  padding: [u8; 5],
}
```

- `mapper_id = (flags7 & 0xF0) | (flags6 >> 4)`.
- `mirroring = ((flags6 >> 2) & 2) | (flags6 & 1)` → 0=H, 1=V, 2=Single0, 3=Single1, 4=Four.
- Если `flags6 & 0x04` — есть 512-байтный trainer перед PRG-ROM.
- PRG-ROM = `prg_rom_nbanks × 0x4000` байт.
- CHR-ROM = `chr_rom_nbanks × 0x2000` байт (если 0 — выделяем 8 KB CHR-RAM).

У нас в `archive/roms/`:
- `mario.nes` — Mapper 1 (MMC1), PRG=16×16 KB, CHR=16×8 KB.
- `nestest.nes` — Mapper 0 (NROM), PRG=1×16 KB, CHR=1×8 KB.

## 4. Мапперы

Картридж отображает адреса CPU/PPU в свои банки ROM/RAM. Самые популярные:

- **Mapper 0 (NROM)** — нет переключения банков. PRG 16 KB (зеркалируется до 32 KB) или 32 KB, CHR 8 KB.
- **Mapper 1 (MMC1)** — 16-битный конфиг-регистр, переключает PRG/CHR банки, управляет mirroring.
- **Mapper 2 (UxROM)** — переключает верхнюю половину PRG, нижняя зафиксирована.
- **Mapper 4 (MMC3)** — IRQ-таймер, сложные банки, используется в больших играх.

`runes` предоставляет `Mapper1`, `Mapper2`, `Mapper4`. **Mapper0 придётся дописать самим** — он тривиален (см. §6.4).

## 5. Крейт `runes` — API

`runes = "0.2.5"` уже добавлен в `Cargo.toml`. Это `#![no_std]` библиотека.

### 5.1 Модули

| Модуль | Что содержит |
|---|---|
| `runes::mos6502` | Структура `CPU<'a>` — эмулятор 6502 |
| `runes::ppu` | `PPU<'a>` + трейт `Screen` |
| `runes::apu` | `APU<'a>` + трейт `Speaker` |
| `runes::memory` | `CPUMemory<'a>`, `PPUMemory<'a>`, трейт `VMem` |
| `runes::mapper` | Трейт `Mapper`, `RefMapper`, `Mapper1/2/4` |
| `runes::cartridge` | Трейт `Cartridge`, `BankType`, `MirrorType` |
| `runes::controller` | Трейты `Controller`, `InputPoller`, `stdctl::Joystick` |
| `runes::utils` | `Read`/`Write` (аналоги std io, для save/load) |

### 5.2 Главный цикл эмулятора (по `src/bin.rs`)

```rust
// 1. Парсим iNES, строим SimpleCart.
let cart = SimpleCart::new(chr_rom, prg_rom, sram, mirror);

// 2. Выбираем маппер по mapper_id.
let mut m: Box<dyn mapper::Mapper> = match mapper_id {
  0 | 2 => Box::new(mapper::Mapper2::new(cart)), // 0 -> Mapper2 (тоже работает для NROM-256K? нет)
  1 => Box::new(mapper::Mapper1::new(cart)),
  4 => Box::new(mapper::Mapper4::new(cart)),
  _ => panic!("unsupported mapper"),
};

// 3. Joystick — реализация InputPoller.
let p1ctl = stdctl::Joystick::new(&event);

// 4. Собираем машину.
let mapper = mapper::RefMapper::new(&mut *m);
let mut cpu = mos6502::CPU::new(CPUMemory::new(&mapper, Some(&p1ctl), None));
let mut ppu = ppu::PPU::new(PPUMemory::new(&mapper), &mut screen);
let mut apu = APU::new(&mut speaker);
let cpu_ptr = &mut cpu as *mut _;
cpu.mem.bus.attach(cpu_ptr, &mut ppu, &mut apu);
cpu.powerup();

// 5. Главный цикл: каждый шаг CPU гонит PPU/APU через bus.tick().
loop {
  while cpu.cycle > 0 { cpu.mem.bus.tick() }
  cpu.step();
}
```

### 5.3 Трейт `ppu::Screen` — куда PPU рисует

```rust
pub trait Screen {
  fn put(&mut self, x: u8, y: u8, color: u8); // один пиксель, color — индекс 0..63
  fn render(&mut self);                       // кадр готов (можно flush)
  fn frame(&mut self);                         // начинается новый кадр
}
```

PPU вызывает `put` для каждого видимого пикселя (256×240 = 61 440 раз за кадр),
потом `render()` и `frame()`. Это **горячий путь** — `put` должен быть максимально дешёвым.

### 5.4 Трейт `apu::Speaker`

```rust
pub trait Speaker {
  fn queue(&mut self, sample: i16); // 16-bit PCM, ~44 100 Гц
}
```

У нас звука пока нет — реализуем `Speaker` с пустой `queue`.

### 5.5 Трейт `controller::InputPoller`

```rust
pub trait InputPoller {
  fn poll(&self) -> u8; // битовая маска stdctl::A/B/SELECT/START/UP/...
}
```

`stdctl::Joystick` оборачивает `InputPoller` и реализует протокол опроса геймпада NES.

## 6. План интеграции в наш проект

### 6.1 Структура файлов

```
src/console/src/
├── main.rs          # точка входа, инициализация железа, главный цикл
├── nes/
│   ├── mod.rs       # pub use, пере-экспорт
│   ├── cart.rs      # SimpleCart + парсинг iNES из static ROM
│   ├── mapper0.rs   # Mapper0 (NROM) — свой
│   ├── screen.rs    # Screen -> наш fb:: (центрирование 256×240 на 480×320)
│   ├── speaker.rs   # Speaker-заглушка
│   ├── input.rs     # InputPoller -> GPIO-кнопки (пока stub)
│   └── palette.rs   # 64-цветная палитра NES -> RGB888
└── ... (существующие модули)
```

### 6.2 Экран: 256×240 → 480×320

NES-кадр 256×240, наш дисплей 480×320. Варианты:

1. **Центрирование 1:1** — рисуем NES-кадр по центру, вокруг чёрная рамка.
   Проще всего, нет масштабирования, минимальная нагрузка.
2. **Целочисленный масштаб ×1** (256×240) — помещается, рамка (480-256)/2=112 по бокам, (320-240)/2=40 сверху/снизу.
3. **Масштаб ×2** — 512×480, не помещается по высоте.
4. **Растягивание** до 480×320 — искажение пропорций, но заполняет экран.

**Рекомендация для первого шага — вариант 2** (центрирование 1:1):
- `Screen::put(x, y, color)` пишет пиксель в `fb` по координатам `(x + OFFSET_X, y + OFFSET_Y)`.
- `OFFSET_X = (480 - 256) / 2 = 112`, `OFFSET_Y = (320 - 240) / 2 = 40`.
- `frame()` — ничего, `render()` — запускает DMA-flush всего буфера (или только изменённой области).

Позже можно добавить целочисленный масштаб ×1 по X и обрезку по Y (256×224 NTSC-visible).

### 6.3 Палитра NES → RGB888

`runes` отдаёт индекс цвета 0..63. В `bin.rs` есть таблица `RGB_COLORS: [u32; 64]`.
Скопируем её в `nes/palette.rs` и сделаем `pub fn rgb(index: u8) -> (u8, u8, u8)`.

```rust
pub const RGB_COLORS: [u32; 64] = [
  0x666666, 0x002a88, /* ... 64 значения ... */ 0x000000,
];

#[inline(always)]
pub fn rgb(color: u8) -> (u8, u8, u8) {
  let c = RGB_COLORS[(color as usize) & 0x3F];
  (((c >> 16) & 0xff) as u8, ((c >> 8) & 0xff) as u8, (c & 0xff) as u8)
}
```

### 6.4 Mapper0 (NROM)

`runes` не предоставляет Mapper0. NROM тривиален:

```rust
pub struct Mapper0<C: Cartridge> {
  cart: C,
  prg_bank: &'a [u8],  // 32 KB (или 16 KB × 2 зеркала)
  chr_bank: &'a [u8],  // 8 KB
  sram: &'a mut [u8],  // 2 KB
}

impl<C: Cartridge> VMem for Mapper0<C> {
  fn read(&self, addr: u16) -> u8 {
    match addr {
      0x0000..=0x1FFF => self.chr_bank[addr as usize],
      0x6000..=0x7FFF => self.sram[(addr - 0x6000) as usize],
      0x8000..=0xFFFF => self.prg_bank[(addr - 0x8000) as usize & (prg_len - 1)],
      _ => 0,
    }
  }
  // write — аналогично, CHR-RAM если chr_len==0
}
```

Если PRG 16 KB — зеркало по маске `0x3FFF`. Если 32 KB — по `0x7FFF`.

### 6.5 `SimpleCart` для static ROM

В архиве `SimpleCart` владеет `Vec<u8>`. В `no_std` мы не можем использовать `Vec`.
Решение: ROM прошивается в бинарник через `include_bytes!`, SRAM — `static mut [u8; 0x2000]`.

```rust
pub static ROM: &[u8] = include_bytes!("../../roms/nestest.nes");

pub struct StaticCart {
  prg: &'static [u8],   // срез из ROM
  chr: &'static mut [u8], // CHR-RAM (static mut) или срез из ROM
  sram: &'static mut [u8],
  mirror: MirrorType,
}
```

Реализуем `Cartridge` для `StaticCart` (аналогично архиву, но без `Vec` и `save/load` — заглушки).

### 6.6 `Screen` для нашего framebuffer

```rust
pub struct FbScreen {
  // ссылку на fb держать нельзя (static mut UB), используем функции fb::
}

impl ppu::Screen for FbScreen {
  #[inline(always)]
  fn put(&mut self, x: u8, y: u8, color: u8) {
    let (r, g, b) = palette::rgb(color);
    // 256×240 по центру 480×320
    fb::set_pixel(112 + x as u16, 40 + y as u16, r, g, b);
  }
  fn render(&mut self) {
    // кадр готов — flush через DMA (вызовет main из-за borrow).
    // Простейший вариант: ставим флаг "нужно flush", main его проверяет.
  }
  fn frame(&mut self) {}
}
```

Проблема: `display.flush_buffer_dma(fb::raw())` требует `&mut display`, а `Screen` не имеет к нему доступа.
Решение: `render()` только **ставит atomic-флаг**, а главный цикл в `main.rs` проверяет флаг и вызывает `display.flush_buffer_dma()`. Для флага — `core::sync::atomic::AtomicBool` (RISC-V поддерживает).

### 6.7 `Speaker` — заглушка

```rust
pub struct NoAudio;
impl apu::Speaker for NoAudio {
  fn queue(&mut self, _sample: i16) {}
}
```

### 6.8 `InputPoller` — заглушка (пока)

```rust
pub struct NoInput;
impl InputPoller for NoInput {
  fn poll(&self) -> u8 { 0 }
}
```

Позже — GPIO-кнопки на каком-нибудь порту (например, PD0..PD7 → A/B/Select/Start/Up/Down/Left/Right).

### 6.9 Главный цикл в `main.rs`

```rust
// После инициализации железа (uart, ccu, spi, dma, display):

// 1. Парсим iNES из static ROM.
let (cart, mapper_id, mirror) = nes::cart::parse(roms::NROMTEST);
// 2. Строим mapper.
let mut m = nes::cart::build_mapper(mapper_id, cart);
// 3. Input + Speaker + Screen.
let p1 = NoInput;
let mut spk = NoAudio;
let mut scr = FbScreen::default();
let p1ctl = stdctl::Joystick::new(&p1);
// 4. Собираем машину.
let mapper = RefMapper::new(&mut *m);
let mut cpu = CPU::new(CPUMemory::new(&mapper, Some(&p1ctl), None));
let mut ppu = PPU::new(PPUMemory::new(&mapper), &mut scr);
let mut apu = APU::new(&mut spk);
let cpu_ptr = &mut cpu as *mut _;
cpu.mem.bus.attach(cpu_ptr, &mut ppu, &mut apu);
cpu.powerup();
// 5. Loop.
loop {
  while cpu.cycle > 0 { cpu.mem.bus.tick() }
  cpu.step();
  if nes::screen::flush_needed() {
    display.flush_buffer_dma(fb::raw());
    nes::screen::clear_flush();
  }
}
```

## 7. Производительность — прикидка

- CPU NES: ~1.79 МГц, ~29 780 циклов на кадр (NTSC).
- На F133 (1 ГГц) — это ~1/560 реального времени. Запас по CPU огромный.
- **Узкое место — SPI-flush**. Сейчас 460 800 байт/кадр через DMA на ~20 МГц SPI:
  460 800 × 8 бит / 20 МГц ≈ 184 мс → ~5 FPS (что и видимаем).
- Для NES-кадра 256×240×3 = 184 320 байт (в 2.5× меньше) → ~73 мс → ~14 FPS.
- Если flush только NES-области (112..368 × 40..280) — те же 184 KB.
- **Дальнейшая оптимизация**: поднять SPI до 40 МГц (×2), либо частичный flush (только изменившиеся строки), либо RGB565 (×1.5 меньше).
- Цель — **30 FPS playable**. 60 FPS (NTSC) на текущем SPI недостижимо без сжатия/частичного обновления.

## 8. Roadmap по шагам

1. **Создать модуль `nes/`** со структурой из §6.1.
2. **`palette.rs`** — скопировать `RGB_COLORS` из `bin.rs`, реализовать `rgb()`.
3. **`cart.rs`** — `StaticCart` + парсинг iNES-заголовка из `include_bytes!`.
4. **`mapper0.rs`** — реализовать `Mapper0` для `nestest.nes`.
5. **`screen.rs`** — `FbScreen` с центрированием и atomic-флагом flush.
6. **`speaker.rs`** и **`input.rs`** — заглушки.
7. **`mod.rs`** — собрать всё, expose `run`-функцию или встроить в `main`.
8. **`main.rs`** — заменить анимацию полос на вызов NES-цикла.
9. **Тест**: запустить `nestest.nes` (он сразу печатает в CPU-RAM, не требует ввода).
   Лог — на UART через `cpu.get_pc()` / trace. Должен пройти ~9000 инструкций без ошибки.
10. **Тест**: запустить `mario.nes` (Mapper 1). Должен показать первый экран игры.
11. **Оптимизация**: поднять SPI до 40 МГц, частичный flush, RGB565 — по мере необходимости.

## 9. Риски и открытые вопросы

- **`runes` использует `MaybeUninit::uninit().assume_init()` и raw pointers** — unsafe, но `no_std`-совместимо. Собирается под `riscv64gc-unknown-none-elf` (проверено).
- **Borrow-чекер**: `runes` хранит `&'a mut dyn Screen` внутри `PPU`, а нам нужно из `main` дёргать `display.flush_buffer_dma()`. Решение — atomic-флаг.
- **Размер ROM**: `mario.nes` ~40 KB, `nestest.nes` ~24 KB — оба влезают в DDR.
- **`Cartridge::save/load`**: в `no_std` без FS — возвращаем `false` или используем заглушку `utils::Read/Write`.
- **`Mapper0` vs `Mapper2`**: в архиве `mapper_id == 0` направлялся в `Mapper2` — это неправильно (Mapper2 переключает банки, NROM не переключает). Нужно свой `Mapper0`.

