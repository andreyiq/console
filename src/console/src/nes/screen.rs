//! Реализация `ppu::Screen` для отдельного NES-буфера 256×240.
//!
//! NES PPU рендерит 256×240 пикселей. Пишем их в отдельный contiguous-буфер
//! NES_BUF (184 KB), потом resample 256×240 → 341×320 (4:3, nearest-neighbor)
//! в STRETCHED_BUF (327 KB), и шлём только его через `flush_region_dma`
//! с окном (69, 0, 341, 320). Чёрная рамка по бокам (69px) остаётся на
//! дисплее с инициализации.
//!
//! `put(x, y, color)` — горячий путь (61 440 раз за кадр), пишем напрямую
//! в NES_BUF без bounds-check (координаты всегда 0..256, 0..240).
//!
//! `render()` — кадр готов, ставим atomic-флаг. Главный цикл в mod.rs
//! проверяет флаг, рисует FPS, зовёт resample и flush_region_dma.

use core::ptr::write_volatile;
use core::sync::atomic::{AtomicBool, Ordering};

use runes::ppu;

use crate::nes::palette;

/// Размеры NES-кадра.
pub const NES_W: usize = 256;
pub const NES_H: usize = 240;
pub const NES_BUF_SIZE: usize = NES_W * NES_H * 3; // 184 320 байт

/// Растянутый кадр в пропорции 4:3 (как на реальном NES-ТВ).
/// 256×240 → 341×320 (×1.333 по обеим осям). nearest-neighbor.
pub const STRETCHED_W: usize = 341;
pub const STRETCHED_H: usize = 320;
pub const STRETCHED_BUF_SIZE: usize = STRETCHED_W * STRETCHED_H * 3; // 327 360 байт

/// Смещение растянутого кадра в центре дисплея 480×320.
/// Слева/справа по чёрной рамке: (480-341)/2 = 69px. По вертикали: (320-320)/2 = 0.
pub const STRETCHED_OFFSET_X: u16 = (crate::display::WIDTH as usize - STRETCHED_W) as u16 / 2; // 69
pub const STRETCHED_OFFSET_Y: u16 = (crate::display::HEIGHT as usize - STRETCHED_H) as u16 / 2; // 0

/// Contiguous-буфер NES-кадра 256×240 RGB888. `static mut` — доступ
/// через безопасные функции ниже (raw_ptr + from_raw_parts).
static mut NES_BUF: [u8; NES_BUF_SIZE] = [0; NES_BUF_SIZE];

/// Растянутый буфер 341×320 RGB888. Получается resample из NES_BUF.
static mut STRETCHED_BUF: [u8; STRETCHED_BUF_SIZE] = [0; STRETCHED_BUF_SIZE];

/// Флаг «кадр готов, пора flush». `render()` ставит true, main — false.
static FLUSH_NEEDED: AtomicBool = AtomicBool::new(false);

/// Проверить флаг flush (вызывает main после каждого cpu.step()).
pub fn flush_needed() -> bool {
  FLUSH_NEEDED.load(Ordering::Relaxed)
}

/// Сбросить флаг flush.
pub fn clear_flush() {
  FLUSH_NEEDED.store(false, Ordering::Relaxed)
}

/// Сырой срез растянутого буфера 341×320 (для flush в дисплей).
pub fn stretched_raw() -> &'static [u8] {
  unsafe {
    let p = core::ptr::addr_of_mut!(STRETCHED_BUF) as *const u8;
    core::slice::from_raw_parts(p, STRETCHED_BUF_SIZE)
  }
}

/// Пересэмплировать NES_BUF (256×240) → STRETCHED_BUF (341×320) nearest-neighbor.
/// Вызывать после того, как NES-кадр полностью отрисован (и FPS нанесён).
/// ~109K итераций по 3 байта — на C906 это единицы мс, боттлнек всё равно SPI.
pub fn resample() {
  unsafe {
    let src = core::ptr::addr_of_mut!(NES_BUF) as *const u8;
    let dst = core::ptr::addr_of_mut!(STRETCHED_BUF) as *mut u8;
    for dy in 0..STRETCHED_H {
      let src_y = (dy * NES_H) / STRETCHED_H;
      let src_row = (src_y * NES_W) as usize * 3;
      let dst_row = (dy * STRETCHED_W) as usize * 3;
      for dx in 0..STRETCHED_W {
        let src_x = (dx * NES_W) / STRETCHED_W;
        let si = src_row + (src_x as usize) * 3;
        let di = dst_row + (dx as usize) * 3;
        let s = src.add(si);
        let d = dst.add(di);
        write_volatile(d, *s);
        write_volatile(d.add(1), *s.add(1));
        write_volatile(d.add(2), *s.add(2));
      }
    }
  }
}

/// Залить прямоугольник в NES-буфере (для подложки под FPS-цифры).
/// Координаты без bounds-check — вызывающий отвечает за валидность.
pub fn fill_rect(x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
  unsafe {
    let p = core::ptr::addr_of_mut!(NES_BUF) as *mut u8;
    for row in y..(y + h) {
      let base = ((row as usize) * NES_W + (x as usize)) * 3;
      for col in 0..(w as usize) {
        let i = base + col * 3;
        write_volatile(p.add(i), r);
        write_volatile(p.add(i + 1), g);
        write_volatile(p.add(i + 2), b);
      }
    }
  }
}

// 3×5 шрифт для цифр 0-9. Каждая цифра — 5 строк по 3 бита.
const DIGITS: [[u8; 5]; 10] = [
  [0b111, 0b101, 0b101, 0b101, 0b111], // 0
  [0b010, 0b110, 0b010, 0b010, 0b111], // 1
  [0b111, 0b001, 0b111, 0b100, 0b111], // 2
  [0b111, 0b001, 0b111, 0b001, 0b111], // 3
  [0b101, 0b101, 0b111, 0b001, 0b001], // 4
  [0b111, 0b100, 0b111, 0b001, 0b111], // 5
  [0b111, 0b100, 0b111, 0b101, 0b111], // 6
  [0b111, 0b001, 0b010, 0b010, 0b010], // 7
  [0b111, 0b101, 0b111, 0b101, 0b111], // 8
  [0b111, 0b101, 0b111, 0b001, 0b111], // 9
];

/// Нарисовать одну цифру (0-9) в (x,y) масштаба `scale` в NES-буфере.
pub fn draw_digit(d: u8, x: u16, y: u16, scale: u16, r: u8, g: u8, b: u8) {
  if d > 9 {
    return;
  }
  let rows = DIGITS[d as usize];
  for (ry, row) in rows.iter().enumerate() {
    for cx in 0..3u16 {
      if (row >> (2 - cx)) & 1 == 1 {
        fill_rect(x + cx * scale, y + (ry as u16) * scale, scale, scale, r, g, b);
      }
    }
  }
}

/// Нарисовать неотрицательное число `n` в (x,y) в NES-буфере.
pub fn draw_number(n: u32, x: u16, y: u16, scale: u16, r: u8, g: u8, b: u8) {
  let digit_w = 3 * scale + scale; // 3 px цифра + 1 px зазор
  if n == 0 {
    draw_digit(0, x, y, scale, r, g, b);
    return;
  }
  let mut digits = [0u8; 10];
  let mut i = 0usize;
  let mut v = n;
  while v > 0 {
    digits[i] = (v % 10) as u8;
    v /= 10;
    i += 1;
  }
  for k in 0..i {
    draw_digit(digits[i - 1 - k], x + (k as u16) * digit_w, y, scale, r, g, b);
  }
}

/// Экран NES, рисующий в NES_BUF.
pub struct FbScreen;

impl FbScreen {
  pub const fn new() -> Self {
    FbScreen
  }
}

impl ppu::Screen for FbScreen {
  /// Один пиксель NES (x: 0..256, y: 0..240, color: индекс палитры 0..63).
  /// Пишем 3 байта RGB888 в NES_BUF по индексу (y*256 + x)*3.
  #[inline(always)]
  fn put(&mut self, x: u8, y: u8, color: u8) {
    let (r, g, b) = palette::rgb(color);
    let i = ((y as usize) * NES_W + (x as usize)) * 3;
    unsafe {
      let p = core::ptr::addr_of_mut!(NES_BUF) as *mut u8;
      write_volatile(p.add(i), r);
      write_volatile(p.add(i + 1), g);
      write_volatile(p.add(i + 2), b);
    }
  }

  /// Кадр полностью отрисован — просим main сделать flush.
  fn render(&mut self) {
    FLUSH_NEEDED.store(true, Ordering::Relaxed)
  }

  /// Начался новый кадр. PPU сам перерисует каждый пиксель — очищать не нужно.
  fn frame(&mut self) {}
}
