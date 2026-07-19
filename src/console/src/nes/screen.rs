//! Framebuffer экрана для NES-эмулятора.
//!
//! Scanline-based подход: PPU рендерит весь кадр (240 строк) одним батчем
//! через put(x, y, color), потом render() flush-ит NES_BUF на дисплей.
//! Никаких per-scanline flush — один большой flush в конце кадра.
//!
//! NES_BUF: 256×240×3 = 184 KB в DDR. put() пишет напрямую по индексу.

use core::sync::atomic::{AtomicU32, Ordering};

use runes::ppu;

use crate::display::Display;
use crate::nes::palette;

/// Размеры NES-кадра.
pub const NES_W: usize = 256;
pub const NES_H: usize = 240;
const NES_BUF_SIZE: usize = NES_W * NES_H * 3;

/// Смещение NES-кадра в центре дисплея 480×320.
pub const OFFSET_X: u16 = (crate::display::WIDTH as usize - NES_W) as u16 / 2; // 112
pub const OFFSET_Y: u16 = (crate::display::HEIGHT as usize - NES_H) as u16 / 2; // 40

/// Framebuffer NES: 256×240 RGB888. В DDR, flush-ится целиком через DMA.
static mut NES_BUF: [u8; NES_BUF_SIZE] = [0; NES_BUF_SIZE];

/// Счётчик кадров (для UART-лога FPS).
static FRAME_COUNTER: AtomicU32 = AtomicU32::new(0);

fn mcycle() -> u64 {
  riscv::register::mcycle::read() as u64
}

/// Экран NES: пишет в NES_BUF, flush-ит на дисплей в render().
pub struct FbScreen {
  display: *const Display,
  last_mcycle: u64,
}

impl FbScreen {
  /// Создать экран. `display` живёт всё время работы эмулятора.
  pub fn new(display: &Display) -> Self {
    Self {
      display: display as *const Display,
      last_mcycle: 0,
    }
  }

  /// Сброс NES_BUF в чёрный (вызывается один раз при старте).
  pub fn clear_buf() {
    unsafe {
      for b in NES_BUF.iter_mut() {
        *b = 0;
      }
    }
  }
}

impl ppu::Screen for FbScreen {
  /// Один пиксель NES. Пишет напрямую в NES_BUF по индексу (y*256 + x)*3.
  #[inline(always)]
  fn put(&mut self, x: u8, y: u8, color: u8) {
    let (r, g, b) = palette::rgb(color);
    let i = ((y as usize) * NES_W + (x as usize)) * 3;
    unsafe {
      NES_BUF[i] = r;
      NES_BUF[i + 1] = g;
      NES_BUF[i + 2] = b;
    }
  }

  /// Кадр готов. Flush NES_BUF на дисплей через DMA (один большой flush).
  fn render(&mut self) {
    unsafe {
      (*self.display).flush_region_dma(
        &NES_BUF,
        OFFSET_X,
        OFFSET_Y,
        NES_W as u16,
        NES_H as u16,
      );
    }
  }

  /// Начался новый кадр. UART-лог FPS каждые 60 кадров.
  fn frame(&mut self) {
    const CPU_HZ: u64 = 1_009_000_000;
    let now = mcycle();
    let frame = FRAME_COUNTER.fetch_add(1, Ordering::Relaxed) + 1;
    if self.last_mcycle != 0 {
      let cpf = now - self.last_mcycle;
      if cpf > 0 && frame % 60 == 0 {
        let fps = (CPU_HZ / cpf) as u32;
        println!("nes: frame {} fps={} cpf={}", frame, fps, cpf);
      }
    }
    self.last_mcycle = now;
  }
}
