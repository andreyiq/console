//! Реализация `ppu::Screen` — двойной буфер NES-кадра 256×240.
//!
//! NES PPU рендерит 256×240 пикселей. Пишем их в contiguous-буфер (184 KB)
//! и шлём через `flush_region_dma` с окном (112, 40, 256, 240) — кадр в
//! натуральном размере по центру дисплея 480×320. Чёрная рамка вокруг
//! остаётся на дисплее с инициализации.
//!
//! Пиксель NES не квадратный, поэтому «правильные» пропорции — это 4:3
//! (341×320). Но растяжение стоит и лишний проход resample по 109K пикселей,
//! и +78% байт в SPI (327 KB против 184 KB, т.е. 131 мс против 74 мс на
//! 20 МГц). Пока мерим и оптимизируем — выводим нативные 256×240, чтобы
//! SPI не маскировал прогресс по CPU.
//!
//! # Зачем два буфера
//!
//! Flush кадра занимает 73 мс (184 КБ на SPI 20 МГц), эмуляция — 27 мс.
//! Если ждать DMA, время кадра = 27 + 73 = 100 мс. С двумя буферами PPU
//! пишет кадр N+1, пока DMA отправляет кадр N, и время кадра становится
//! `max(27, 73)` вместо суммы.
//!
//! `write` — буфер, в который PPU пишет прямо сейчас. `READY_IDX` — буфер,
//! который уже закончен и отдан на flush. `render()` вызывается PPU на
//! scanline 241 (то есть все 240 видимых строк уже нарисованы), там мы
//! публикуем законченный буфер и переключаем `write` на другой.
//!
//! # Про `dirty`
//!
//! `put()` вызывается только когда рендеринг включён (`runes/ppu.rs:590`:
//! `if (pre_line || visible_line) && rendering`). Mario гасит экран на время
//! смены уровня, и тогда за кадр не приходит ни одного пикселя. При наивной
//! двойной буферизации мы бы в эти кадры показывали попеременно два разных
//! старых кадра — то есть мигание. Поэтому считаем, был ли хоть один `put()`,
//! и если нет — не публикуем буфер и не флашим вовсе. Дисплей просто держит
//! последнюю картинку (и мы экономим 73 мс SPI).

use core::ptr::write_volatile;
use core::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use runes::ppu;

use crate::nes::palette;

/// Размеры NES-кадра.
pub const NES_W: usize = 256;
pub const NES_H: usize = 240;
pub const NES_BUF_SIZE: usize = NES_W * NES_H * 3; // 184 320 байт

/// Смещение NES-кадра в центре дисплея 480×320.
/// По горизонтали (480-256)/2 = 112px, по вертикали (320-240)/2 = 40px.
pub const NES_OFFSET_X: u16 = (crate::display::WIDTH as usize - NES_W) as u16 / 2; // 112
pub const NES_OFFSET_Y: u16 = (crate::display::HEIGHT as usize - NES_H) as u16 / 2; // 40

/// Два буфера кадра RGB888 (368 КБ в .bss). Доступ — через функции ниже.
static mut NES_BUF: [[u8; NES_BUF_SIZE]; 2] = [[0; NES_BUF_SIZE]; 2];

/// Индекс законченного буфера, который надо отправить на дисплей.
static READY_IDX: AtomicUsize = AtomicUsize::new(0);

/// Флаг «кадр готов, пора flush». `render()` ставит true, main — false.
static FLUSH_NEEDED: AtomicBool = AtomicBool::new(false);

// Здесь временно жил `ABLATION` — переключатель режимов `put()` для замера
// стоимости вывода пикселей. Замер сделан (результаты в AGENTS.md: всё тело
// `put()` — 1.4 мс из 27), код убран: сама проверка режима стоила ~1 мс на
// кадр. Если понадобится повторить — ветвление в `put()` + переключение
// каждые N кадров из главного цикла.

/// Указатель на начало буфера `i`.
fn buf_ptr(i: usize) -> *mut u8 {
  unsafe { (core::ptr::addr_of_mut!(NES_BUF) as *mut u8).add(i * NES_BUF_SIZE) }
}

/// Проверить флаг flush (вызывает main после каждого cpu.step()).
pub fn flush_needed() -> bool {
  FLUSH_NEEDED.load(Ordering::Relaxed)
}

/// Сбросить флаг flush.
pub fn clear_flush() {
  FLUSH_NEEDED.store(false, Ordering::Relaxed)
}

/// Срез законченного буфера — его и отправляем на дисплей.
///
/// Пока DMA его читает, PPU уже пишет в другой буфер, поэтому данные под
/// DMA не меняются.
pub fn ready_raw() -> &'static [u8] {
  unsafe { core::slice::from_raw_parts(buf_ptr(READY_IDX.load(Ordering::Relaxed)), NES_BUF_SIZE) }
}

/// Указатель на законченный буфер — для оверлея FPS поверх готового кадра.
pub fn ready_ptr() -> *mut u8 {
  buf_ptr(READY_IDX.load(Ordering::Relaxed))
}

/// Залить прямоугольник в буфере `p` (для подложки под FPS-цифры).
/// Координаты без bounds-check — вызывающий отвечает за валидность.
pub fn fill_rect(p: *mut u8, x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
  unsafe {
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

/// Нарисовать одну цифру (0-9) в (x,y) масштаба `scale` в буфере `p`.
pub fn draw_digit(p: *mut u8, d: u8, x: u16, y: u16, scale: u16, r: u8, g: u8, b: u8) {
  if d > 9 {
    return;
  }
  let rows = DIGITS[d as usize];
  for (ry, row) in rows.iter().enumerate() {
    for cx in 0..3u16 {
      if (row >> (2 - cx)) & 1 == 1 {
        fill_rect(
          p,
          x + cx * scale,
          y + (ry as u16) * scale,
          scale,
          scale,
          r,
          g,
          b,
        );
      }
    }
  }
}

/// Нарисовать неотрицательное число `n` в (x,y) в буфере `p`.
pub fn draw_number(p: *mut u8, n: u32, x: u16, y: u16, scale: u16, r: u8, g: u8, b: u8) {
  let digit_w = 3 * scale + scale; // 3 px цифра + 1 px зазор
  if n == 0 {
    draw_digit(p, 0, x, y, scale, r, g, b);
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
    draw_digit(p, digits[i - 1 - k], x + (k as u16) * digit_w, y, scale, r, g, b);
  }
}

/// Экран NES с двойной буферизацией.
pub struct FbScreen {
  /// Буфер, в который PPU пишет пиксели текущего кадра.
  write: *mut u8,
  /// Индекс этого буфера (0 или 1).
  idx: usize,
  /// Был ли хоть один `put()` в текущем кадре (см. модульный комментарий).
  dirty: bool,
}

impl FbScreen {
  pub fn new() -> Self {
    // Пишем в буфер 1, а буфер 0 считается «готовым» — на первом кадре он
    // просто чёрный, и до первого render() его никто не флашит.
    FbScreen {
      write: buf_ptr(1),
      idx: 1,
      dirty: false,
    }
  }
}

impl ppu::Screen for FbScreen {
  /// Один пиксель NES (x: 0..256, y: 0..240, color: индекс палитры 0..63).
  /// Пишем 3 байта RGB888 в активный буфер по индексу (y*256 + x)*3.
  ///
  /// Записи обычные, не `volatile`: буфер — простая RAM, а не регистры.
  /// `volatile` тут только мешал бы компилятору их объединять и держать
  /// значения в регистрах. Видимость для DMA обеспечивает не он, а
  /// `cache::clean_dcache()` (там `fence rw, rw` + `dcache.call`), а от
  /// выкидывания записей защищает то, что буфер потом читается через
  /// `ready_raw()`.
  #[inline(always)]
  fn put(&mut self, x: u8, y: u8, color: u8) {
    let (r, g, b) = palette::rgb(color);
    let i = ((y as usize) * NES_W + (x as usize)) * 3;
    self.dirty = true;
    unsafe {
      let p = self.write.add(i);
      p.write(r);
      p.add(1).write(g);
      p.add(2).write(b);
    }
  }

  /// Кадр полностью отрисован (PPU зовёт это на scanline 241, то есть все
  /// 240 видимых строк уже в буфере). Публикуем его на flush и переключаем
  /// запись на другой буфер — PPU сразу начнёт кадр N+1, пока DMA гонит N.
  ///
  /// Если за кадр не было ни одного пикселя (экран погашен) — не публикуем
  /// и не переключаемся, чтобы не мигать двумя старыми кадрами.
  fn render(&mut self) {
    if !self.dirty {
      return;
    }
    READY_IDX.store(self.idx, Ordering::Relaxed);
    FLUSH_NEEDED.store(true, Ordering::Relaxed);
    self.idx ^= 1;
    self.write = buf_ptr(self.idx);
  }

  /// Начался новый кадр. PPU перерисует каждый пиксель сам, очищать не нужно.
  fn frame(&mut self) {
    self.dirty = false;
  }
}
