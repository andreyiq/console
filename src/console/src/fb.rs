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
