//! Реализация `ppu::Screen` для нашего framebuffer.
//!
//! NES PPU рендерит 256×240 пикселей. Мы центрируем их на 480×320 дисплее:
//!   offset_x = (480 - 256) / 2 = 112
//!   offset_y = (320 - 240) / 2 = 40
//!
//! `put(x, y, color)` — горячий путь (61 440 раз за кадр), пишем напрямую в
//! `fb::FRAMEBUFFER` без bounds-check (координаты всегда валидны).
//!
//! `render()` — кадр готов, ставим atomic-флаг. Главный цикл в `main`
//! проверяет флаг и зовёт `display.flush_buffer_dma(fb::raw())`.

use core::ptr::write_volatile;
use core::sync::atomic::{AtomicBool, Ordering};

use runes::ppu;

use crate::fb;
use crate::nes::palette;

/// Смещение NES-кадра 256×240 в центре дисплея 480×320.
pub const OFFSET_X: u16 = (crate::display::WIDTH - 256) / 2; // 112
pub const OFFSET_Y: u16 = (crate::display::HEIGHT - 240) / 2; // 40

/// Флаг «кадр готов, пора flush». `render()` ставит true, main — false.
static FLUSH_NEEDED: AtomicBool = AtomicBool::new(false);

/// Проверить флаг flush (вызывает main после каждого cpu.step()).
pub fn flush_needed() -> bool {
  FLUSH_NEEDED.load(Ordering::Relaxed)
}

/// Сбросить флаг flush (после того как display.flush_buffer_dma отработал).
pub fn clear_flush() {
  FLUSH_NEEDED.store(false, Ordering::Relaxed);
}

/// Экран NES, рисующий прямо в наш framebuffer.
pub struct FbScreen;

impl FbScreen {
  pub const fn new() -> Self {
    FbScreen
  }
}

impl ppu::Screen for FbScreen {
  /// Один пиксель NES (x: 0..256, y: 0..240, color: индекс палитры 0..63).
  /// Пишем 3 байта RGB888 в framebuffer по адресу (OFFSET_X + x, OFFSET_Y + y).
  #[inline(always)]
  fn put(&mut self, x: u8, y: u8, color: u8) {
    let (r, g, b) = palette::rgb(color);
    let fx = OFFSET_X + x as u16;
    let fy = OFFSET_Y + y as u16;
    let i = ((fy as usize) * (crate::display::WIDTH as usize) + (fx as usize)) * 3;
    // FRAMEBUFFER — `static mut` в fb.rs. Пишем напрямую через volatile,
    // чтобы компилятор не переупорядочил и не объединил записи.
    unsafe {
      let p = fb::raw_ptr().add(i);
      write_volatile(p, r);
      write_volatile(p.add(1), g);
      write_volatile(p.add(2), b);
    }
  }

  /// Кадр полностью отрисован — просим main сделать flush.
  fn render(&mut self) {
    FLUSH_NEEDED.store(true, Ordering::Relaxed);
  }

  /// Начался новый кадр. Очищать ничего не нужно — PPU сам перерисует каждый
  /// пиксель, а фон вокруг NES-кадра остаётся чёрным из инициализации fb.
  fn frame(&mut self) {}
}
