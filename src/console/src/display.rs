//! ILI9488 TFT-дисплей по SPI. 320×480, RGB565.
//!
//! Драйвер обёртывает `Spi` и управляет двумя доп. пинами:
//!   DC  (PE0) — Data/Command: 0 = команда, 1 = данные
//!   RST (PE1) — hardware reset
//!
//! CS управляется внутри каждого метода (cs_low в начале, cs_high в конце).
//!
//! Init-последовательность из docs/display/ili9488/BOE3.5IPS-ILI9488.TXT.

use crate::gpio::{self, Pin};
use crate::spi::Spi;
use crate::utils;

/// Размер экрана (ориентация MADCTL=0xE8 → landscape).
pub const WIDTH: u16 = 480;
pub const HEIGHT: u16 = 320;

/// ILI9488 дисплей на SPI + DC/RST пинах.
pub struct Display {
  spi: Spi,
  dc: Pin,
  rst: Pin,
}

impl Display {
  pub fn new(spi: Spi, dc: Pin, rst: Pin) -> Self {
    Self { spi, dc, rst }
  }

  /// DC = 0 (команда).
  #[inline]
  fn dc_command(&self) {
    self.dc.set_low();
  }

  /// DC = 1 (данные).
  #[inline]
  fn dc_data(&self) {
    self.dc.set_high();
  }

  /// Отправить команду (DC=0, CS=low).
  fn command(&self, cmd: u8) {
    self.spi.cs_low();
    self.dc_command();
    self.spi.send_byte(cmd);
    self.spi.cs_high();
  }

  /// Отправить команду + массив данных (одна транзакция под одним CS).
  fn command_data(&self, cmd: u8, data: &[u8]) {
    self.spi.cs_low();
    self.dc_command();
    self.spi.send_byte(cmd);
    if !data.is_empty() {
      self.dc_data();
      self.spi.send(data);
    }
    self.spi.cs_high();
  }

  /// Hardware reset: RST низко на ~50 ms, высоко на ~150 ms.
  fn hardware_reset(&self) {
    self.rst.set_low();
    utils::delay(50_000);
    self.rst.set_high();
    utils::delay(150_000);
  }

  /// Полная инициализация: reset + init-последовательность + Sleep Out + Display On.
  pub fn init(&self) {
    // Пины DC и RST как выходы
    self.dc.set_func(gpio::Func::Output);
    self.rst.set_func(gpio::Func::Output);
    self.dc.set_high();
    self.rst.set_high();

    self.hardware_reset();

    self.command_data(0xF7, &[0xA9, 0x51, 0x2C, 0x82]); // Adjust Control 3
    self.command_data(0xC0, &[0x0F, 0x0F]); // Power Control 1
    self.command_data(0xC1, &[0x47]); // Power Control 2
    self.command_data(0xC5, &[0x00, 0x4D, 0x80]); // VCOM Control
    self.command_data(0xB1, &[0xB0, 0x11]); // Frame Rate Control
    self.command_data(0xB4, &[0x02]); // Display Inversion Control
    self.command_data(0x36, &[0xE8]); // MADCTL: landscape, BGR
    self.command_data(0x3A, &[0x55]); // Pixel Format = RGB565
    self.command(0x21); // Display Inversion ON (IPS)
    self.command_data(0xE9, &[0x00]); // Set Image Function
    self.command_data(0xF7, &[0xA9, 0x51, 0x2C, 0x82]); // Adjust Control 3 (повтор)
    self.command_data(
      0xE0, // Positive Gamma
      &[
        0x00, 0x07, 0x0B, 0x03, 0x0F, 0x05, 0x30, 0x56, 0x47, 0x04, 0x0B, 0x0A, 0x2D, 0x37, 0x0F,
      ],
    );
    self.command_data(
      0xE1, // Negative Gamma
      &[
        0x00, 0x0E, 0x13, 0x04, 0x11, 0x07, 0x39, 0x45, 0x50, 0x07, 0x10, 0x0D, 0x32, 0x36, 0x0F,
      ],
    );
    self.command(0x11); // Sleep Out
    utils::delay(480_000); // ~120 ms
    self.command(0x29); // Display ON
    utils::delay(80_000); // ~20 ms
  }

  /// Задать окно для рисования (Column + Page Address Set).
  pub fn set_window(&self, x: u16, y: u16, w: u16, h: u16) {
    let x_end = x + w - 1;
    let y_end = y + h - 1;
    self.command_data(
      0x2A, // Column Address Set: [start_hi, start_lo, end_hi, end_lo]
      &[
        (x >> 8) as u8,
        (x & 0xFF) as u8,
        (x_end >> 8) as u8,
        (x_end & 0xFF) as u8,
      ],
    );
    self.command_data(
      0x2B, // Page Address Set: [start_hi, start_lo, end_hi, end_lo]
      &[
        (y >> 8) as u8,
        (y & 0xFF) as u8,
        (y_end >> 8) as u8,
        (y_end & 0xFF) as u8,
      ],
    );
  }

  /// Залить весь экран цветом RGB565.
  ///
  /// Медленно (побайтово через SPI на делителе 0x1000) — для первого урока.
  /// Ускорим в Главе 5 (буфер) и Главе 6 (DMA).
  pub fn fill(&self, color: u16) {
    self.set_window(0, 0, WIDTH, HEIGHT);

    // Memory Write — одна длинная транзакция: CS низко на все пиксели
    self.spi.cs_low();
    self.dc_command();
    self.spi.send_byte(0x2C);
    self.dc_data();

    let hi = (color >> 8) as u8;
    let lo = (color & 0xFF) as u8;
    let total = WIDTH as u32 * HEIGHT as u32;
    for _ in 0..total {
      self.spi.send_byte(hi);
      self.spi.send_byte(lo);
    }
    self.spi.cs_high();
  }
}
