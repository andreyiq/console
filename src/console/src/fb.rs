//! Framebuffer в RAM (RGB888, 480×320). Рисуешь в массив в памяти —
//! потом один flush() отправляет весь буфер на дисплей через SPI.
//!
//! Плюс: рисование в RAM мгновенное (один `store` на пиксель), без SPI-накладных.
//! Минус: flush() всё равно идёт через SPI — ускорим в Главе 6 (DMA).

use core::ptr::addr_of_mut;

use crate::display::{HEIGHT, WIDTH};

/// 3 байта на пиксель (RGB888).
pub const BYTES_PER_PIXEL: usize = 3;
/// Размер буфера: 480 × 320 × 3 = 460 800 байт (~450 KB). Лежит в DDR (64 MB).
pub const FB_SIZE: usize = (WIDTH as usize) * (HEIGHT as usize) * BYTES_PER_PIXEL;

/// Сам буфер. `static mut` — доступ через безопасные функции ниже.
static mut FRAMEBUFFER: [u8; FB_SIZE] = [0; FB_SIZE];

/// Mutable срез всего буфера (через raw-указатель, без `static_mut_refs` UB).
#[inline]
fn as_mut_slice() -> &'static mut [u8] {
  unsafe {
    let ptr = addr_of_mut!(FRAMEBUFFER) as *mut u8;
    core::slice::from_raw_parts_mut(ptr, FB_SIZE)
  }
}

/// Immutable срез всего буфера.
#[inline]
fn as_slice() -> &'static [u8] {
  unsafe {
    let ptr = addr_of_mut!(FRAMEBUFFER) as *const u8;
    core::slice::from_raw_parts(ptr, FB_SIZE)
  }
}

/// Индекс байта в буфере для пикселя (x, y). Без bounds-check —
/// вызывающий должен гарантировать 0 ≤ x < WIDTH, 0 ≤ y < HEIGHT.
#[inline]
fn idx(x: u16, y: u16) -> usize {
  ((y as usize) * (WIDTH as usize) + (x as usize)) * BYTES_PER_PIXEL
}

/// Залить весь буфер одним цветом.
pub fn clear(r: u8, g: u8, b: u8) {
  for chunk in as_mut_slice().chunks_exact_mut(BYTES_PER_PIXEL) {
    chunk[0] = r;
    chunk[1] = g;
    chunk[2] = b;
  }
}

/// Поставить один пиксель. Координаты за пределами экрана игнорируются.
pub fn set_pixel(x: u16, y: u16, r: u8, g: u8, b: u8) {
  if x >= WIDTH || y >= HEIGHT {
    return;
  }
  let i = idx(x, y);
  let buf = as_mut_slice();
  buf[i] = r;
  buf[i + 1] = g;
  buf[i + 2] = b;
}

/// Залить прямоугольник в буфере. Без bounds-check — вызывающий отвечает за координаты.
pub fn fill_rect(x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
  let buf = as_mut_slice();
  for row in y..(y + h) {
    let base = idx(x, row);
    for c in 0..(w as usize * BYTES_PER_PIXEL) {
      buf[base + c] = match c % 3 {
        0 => r,
        1 => g,
        _ => b,
      };
    }
  }
}

/// Горизонтальная линия.
pub fn draw_h_line(x: u16, y: u16, len: u16, r: u8, g: u8, b: u8) {
  fill_rect(x, y, len, 1, r, g, b);
}

/// Вертикальная линия.
pub fn draw_v_line(x: u16, y: u16, len: u16, r: u8, g: u8, b: u8) {
  fill_rect(x, y, 1, len, r, g, b);
}

/// Контур прямоугольника (1 px).
pub fn draw_rect(x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
  if w == 0 || h == 0 {
    return;
  }
  draw_h_line(x, y, w, r, g, b);
  draw_h_line(x, y + h - 1, w, r, g, b);
  draw_v_line(x, y, h, r, g, b);
  draw_v_line(x + w - 1, y, h, r, g, b);
}

/// Доступ к сырым байтам буфера (для flush в дисплей).
pub fn raw() -> &'static [u8] {
  as_slice()
}

/// Сырой mutable-указатель на начало буфера. Для горячего пути рисования
/// (NES PPU put) — без создания `&mut` ссылки, чтобы не конфликтовать
/// с `raw()`. Вызывающий отвечает за алиасинг.
pub fn raw_ptr() -> *mut u8 {
  addr_of_mut!(FRAMEBUFFER) as *mut u8
}

// 3×5 шрифт для цифр 0-9. Каждая цифра — 5 строк по 3 бита (бит 2=лев, 1=центр, 0=прав).
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

/// Нарисовать одну цифру (0-9) в (x,y), масштаб `scale` (1 = 3×5 px, 4 = 12×20 px).
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

/// Нарисовать неотрицательное число `n` в (x,y). Ширина цифры = 3*scale + scale (зазор).
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
