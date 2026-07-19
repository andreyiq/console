//! SPI0 драйвер для F133. Раздел 9.3.6 user manual.
//!
//! Управление CS — ручное через GPIO PC3 (function 1 = output).
//! Это даёт полный контроль: CS можно держать низким между отправками байтов,
//! что удобно для ILI9488 (одно «сообщение» = команда + данные под одним CS).

use core::ptr::{read_volatile, write_volatile};

use crate::gpio;

const SPI0_BASE: u32 = 0x0402_5000;

// Регистры SPI0 (смещения от SPI0_BASE)
const GCR: *mut u32 = (SPI0_BASE + 0x04) as *mut u32; // Global Control
const TCR: *mut u32 = (SPI0_BASE + 0x08) as *mut u32; // Transfer Control
const FCR: *mut u32 = (SPI0_BASE + 0x18) as *mut u32; // FIFO Control
const CLK_CTL: *mut u32 = (SPI0_BASE + 0x24) as *mut u32; // Clock divider
const MBC: *mut u32 = (SPI0_BASE + 0x30) as *mut u32; // Master Burst Counter
const MTC: *mut u32 = (SPI0_BASE + 0x34) as *mut u32; // Master Transmit Counter
const BCC: *mut u32 = (SPI0_BASE + 0x38) as *mut u32; // Master Burst Control
const TXD: *mut u32 = (SPI0_BASE + 0x200) as *mut u32; // TX Data

// Биты TCR
const XCH: u32 = 1 << 31; // Exchange Burst — старт передачи (auto-clear по окончании)
const DHB: u32 = 1 << 8; // Discard Hash Burst — только TX, без приёма

// Биты GCR
const GCR_EN: u32 = 0x83; // TP_EN | MODE (master) | EN

#[inline]
fn write_reg(reg: *mut u32, val: u32) {
  unsafe {
    write_volatile(reg, val);
  }
}

#[inline]
fn read_reg(reg: *mut u32) -> u32 {
  unsafe { read_volatile(reg) }
}

/// Опустить CS (активный, низкий уровень) — PC3 = 0.
pub fn cs_low() {
  gpio::pc_set_low(gpio::PinC::P3);
}

/// Поднять CS (неактивный, высокий уровень) — PC3 = 1.
pub fn cs_high() {
  gpio::pc_set_high(gpio::PinC::P3);
}

/// Инициализация SPI0: пины, делитель, FIFO, режим master TX-only.
/// Перед вызовом нужно включить тактирование через `ccu::Peripheral::Spi0.enable()`.
pub fn init() {
  // PC2,4,5 = SPI0 (func 2), PC3 = GPIO output (ручной CS)
  gpio::pc_set_func(gpio::PinC::P2, gpio::Func::Spi0);
  gpio::pc_set_func(gpio::PinC::P3, gpio::Func::Output);
  gpio::pc_set_func(gpio::PinC::P4, gpio::Func::Spi0);
  gpio::pc_set_func(gpio::PinC::P5, gpio::Func::Spi0);
  cs_high();

  write_reg(CLK_CTL, 0x1000); // делитель — медленно, удобно для анализатора
  write_reg(FCR, 0x8000_8000); // сброс TX и RX FIFO
  write_reg(FCR, 0x10001); // порог срабатывания = 1 байт
  write_reg(TCR, DHB); // только TX, без приёма
  write_reg(GCR, GCR_EN); // master mode + enable
}

/// Отправить один байт по SPI0. CS управляется отдельно (cs_low/cs_high).
pub fn send_byte(byte: u8) {
  write_reg(BCC, 1);
  write_reg(MBC, 1);
  write_reg(MTC, 1);
  write_reg(TXD, byte as u32);
  write_reg(TCR, XCH | DHB); // старт
  while read_reg(TCR) & XCH != 0 {
    core::hint::spin_loop();
  }
}

/// Отправить буфер по SPI0. Длина до 0xFFFFFF байт (ограничение регистров MBC/MTC).
pub fn send(buf: &[u8]) {
  let n = buf.len() as u32;
  if n == 0 {
    return;
  }
  write_reg(BCC, n);
  write_reg(MBC, n);
  write_reg(MTC, n);
  for &b in buf {
    write_reg(TXD, b as u32);
  }
  write_reg(TCR, XCH | DHB);
  while read_reg(TCR) & XCH != 0 {
    core::hint::spin_loop();
  }
}
