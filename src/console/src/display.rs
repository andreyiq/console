//! ILI9488 TFT-дисплей по SPI. 480×320 (landscape), RGB888 (3 байта/пиксель).
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

/// Размер экрана (MADCTL=0xE8 → landscape, как в архиве ili9844.rs).
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

  /// Hardware reset: RST низко ~5 ms, высоко ~120 ms.
  /// По даташиту ILI9488: RST low ≥ 10 µs, после RST high ~120 ms перед командами.
  fn hardware_reset(&self) {
    self.rst.set_low();
    utils::delay(500_000); // ~5 ms
    self.rst.set_high();
    utils::delay(12_000_000); // ~120 ms
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
    self.command_data(0x36, &[0xE8]); // MADCTL: landscape, BGR (как в архиве ili9844.rs)
                                      // 0x3A (pixel format) НЕ отправляем — дисплей использует дефолтный RGB888 (3 байта/пиксель).
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
    utils::delay(50_000_000); // ~500 ms (BOE doc: Delay(480) = 480 ms)
    self.command(0x29); // Display ON
    utils::delay(5_000_000); // ~50 ms
  }

  /// Прочитать регистр дисплея (1 байт ответа). cmd=0x0B для MADCTL, 0x0A для Power Mode.
  pub fn read_reg_1byte(&self, cmd: u8) -> u8 {
    let mut out = [0u8; 4];
    self.spi.cs_low();
    self.dc_command();
    // cmd + 1 dummy + 1 data = 3 байта, читаем 3 (data в 3-й позиции).
    self.spi.send_recv(&[cmd, 0x00, 0x00]);
    self.spi.read_rx(&mut out);
    self.spi.cs_high();
    out[2]
  }

  /// Прочитать ID дисплея (команда 0x04). Возвращает 4 байта, обычно
  /// [0x00, 0x94, 0x88, 0x00] для ILI9488.
  pub fn read_id(&self) -> [u8; 4] {
    let mut out = [0u8; 6];
    self.spi.cs_low();
    self.dc_command();
    self.spi.send_recv(&[0x04, 0x00, 0x00, 0x00, 0x00, 0x00]);
    self.spi.read_rx(&mut out);
    self.spi.cs_high();
    [out[2], out[3], out[4], out[5]]
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

  /// Отправить сырой буфер (RGB888, WIDTH×HEIGHT×3 байт) на весь экран.
  /// Чанкуется по 64 байта (глубина TX FIFO) — каждый чанк отдельный burst.
  pub fn flush_buffer(&self, buf: &[u8]) {
    self.set_window(0, 0, WIDTH, HEIGHT);
    self.spi.cs_low();
    self.dc_command();
    self.spi.send_byte(0x2C);
    self.dc_data();
    const CHUNK: usize = 64; // глубина TX FIFO
    for chunk in buf.chunks(CHUNK) {
      self.spi.send(chunk);
    }
    self.spi.cs_high();
  }

  /// Залить прямоугольник цветом RGB888. Базовый примитив для всех остальных.
  ///
  /// Один set_window + одна транзакция Memory Write на весь прямоугольник —
  /// поэтому fill_rect на N пикселей работает сильно быстрее, чем N вызовов
  /// draw_pixel (один set_window на весь блок vs один set_window на каждый пиксель).
  pub fn fill_rect(&self, x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
    if w == 0 || h == 0 {
      return;
    }
    self.set_window(x, y, w, h);

    self.spi.cs_low();
    self.dc_command();
    self.spi.send_byte(0x2C);
    self.dc_data();

    let pixel = [r, g, b];
    let total = w as u32 * h as u32;
    for _ in 0..total {
      self.spi.send(&pixel);
    }
    self.spi.cs_high();
  }

  /// Залить весь экран цветом RGB888.
  pub fn fill_rgb(&self, r: u8, g: u8, b: u8) {
    self.fill_rect(0, 0, WIDTH, HEIGHT, r, g, b);
  }

  /// Нарисовать один пиксель. Медленно (set_window на каждый пиксель) —
  /// для отдельных точек. Для линий/фигур используй fill_rect/draw_*_line.
  pub fn draw_pixel(&self, x: u16, y: u16, r: u8, g: u8, b: u8) {
    self.fill_rect(x, y, 1, 1, r, g, b);
  }

  /// Горизонтальная линия длиной `len` от (x, y) вправо.
  pub fn draw_h_line(&self, x: u16, y: u16, len: u16, r: u8, g: u8, b: u8) {
    self.fill_rect(x, y, len, 1, r, g, b);
  }

  /// Вертикальная линия длиной `len` от (x, y) вниз.
  pub fn draw_v_line(&self, x: u16, y: u16, len: u16, r: u8, g: u8, b: u8) {
    self.fill_rect(x, y, 1, len, r, g, b);
  }

  /// Прямоугольник (контур) толщиной 1 px.
  pub fn draw_rect(&self, x: u16, y: u16, w: u16, h: u16, r: u8, g: u8, b: u8) {
    if w == 0 || h == 0 {
      return;
    }
    self.draw_h_line(x, y, w, r, g, b); // верх
    self.draw_h_line(x, y + h - 1, w, r, g, b); // низ
    self.draw_v_line(x, y, h, r, g, b); // лево
    self.draw_v_line(x + w - 1, y, h, r, g, b); // право
  }

  /// Залить весь экран цветом RGB565. (Совместимость со старым API.)
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
