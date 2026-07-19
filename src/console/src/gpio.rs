//! GPIO для F133 (Allwinner D1s). Базовый слой для управления пинами.
//!
//! Адреса регистров PIO — из f133_user_manual_v1.0.txt, раздел 9.7.
//! Каждый порт (PB..PG) имеет свой блок регистров со смещением 0x30:
//!   Pn_CFG0  — конфигурация пинов (4 бита на пин)
//!   Pn_DAT   — данные (уровни)              [+0x10]
//!   Pn_DRV0  — сила драйвера                 [+0x14]
//!   Pn_PULL0 — pull-up/down (2 бита на пин) [+0x24]
//!
//! API: методы на типе `Pin` — `PC2.set_func(Func::Spi0); PC3.set_high();`.
//! Именованные пины — `const`-константы внизу файла.

use core::ptr::{read_volatile, write_volatile};

pub const PIO_BASE: u32 = 0x0200_0000;

/// Порт GPIO. У F133 шесть портов: PB, PC, PD, PE, PF, PG.
#[derive(Clone, Copy)]
pub enum Port {
  B,
  C,
  D,
  E,
  F,
  G,
}

/// Функция пина (4 бита в Pn_CFG0). Значения — регистровые.
///
/// ВАЖНО: варианты `Spi0`/`Spi1`/`Uart0` привязаны к конкретным портам:
///   `Spi0`  → PC (func=0b0010)
///   `Spi1`  → PD (func=0b0100)
///   `Uart0` → PE (func=0b0110)
/// Передача «не своего» варианта в пин другого порта скомпилируется,
/// но настроит пин на чужую функцию (см. таблицу 9.7 в user manual).
#[derive(Clone, Copy)]
#[repr(u32)]
pub enum Func {
  Input = 0b0000,
  Output = 0b0001,
  Spi0 = 0b0010,
  Spi1 = 0b0100,
  Uart0 = 0b0110,
  IoDisable = 0b1111,
}

/// Pull-up/down (2 бита на пин в Pn_PULL0).
#[derive(Clone, Copy)]
#[repr(u32)]
pub enum Pull {
  UpDownDisabled = 0b00,
  Up = 0b01,
  Down = 0b10,
}

/// Пин GPIO. Хранит порт и номер ножки (0..31).
/// Именованные константы (`PC2`, `PD10`, `PE2`, ...) — внизу файла.
#[derive(Clone, Copy)]
pub struct Pin {
  port: Port,
  pin: u32,
}

impl Pin {
  pub const fn new(port: Port, pin: u32) -> Self {
    Self { port, pin }
  }

  /// Базовое смещение CFG0 для порта. У F133 каждый порт занимает 0x30 байт.
  fn cfg0_offset(&self) -> u32 {
    match self.port {
      Port::B => 0x30,
      Port::C => 0x60,
      Port::D => 0x90,
      Port::E => 0xC0,
      Port::F => 0xF0,
      Port::G => 0x120,
    }
  }

  fn cfg0_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset()) as *mut u32
  }

  fn dat_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset() + 0x10) as *mut u32
  }

  fn pull0_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset() + 0x24) as *mut u32
  }

  /// Установить функцию пина. В Pn_CFG0 пин `n` занимает биты [4n+3 : 4n].
  #[inline]
  pub fn set_func(&self, f: Func) {
    let shift = self.pin * 4;
    let mask = 0xF << shift;
    unsafe {
      let old = read_volatile(self.cfg0_addr());
      write_volatile(self.cfg0_addr(), (old & !mask) | ((f as u32) << shift));
    }
  }

  /// Поднять пин в 1 (запись 1 в соответствующий бит Pn_DAT).
  #[inline]
  pub fn set_high(&self) {
    let bit = 1u32 << self.pin;
    unsafe {
      let v = read_volatile(self.dat_addr());
      write_volatile(self.dat_addr(), v | bit);
    }
  }

  /// Опустить пин в 0 (запись 0 в соответствующий бит Pn_DAT).
  #[inline]
  pub fn set_low(&self) {
    let bit = 1u32 << self.pin;
    unsafe {
      let v = read_volatile(self.dat_addr());
      write_volatile(self.dat_addr(), v & !bit);
    }
  }

  /// Установить pull-up/down. В Pn_PULL0 пин `n` занимает биты [2n+1 : 2n].
  #[inline]
  pub fn set_pull(&self, p: Pull) {
    let shift = self.pin * 2;
    let mask = 0b11 << shift;
    unsafe {
      let old = read_volatile(self.pull0_addr());
      write_volatile(self.pull0_addr(), (old & !mask) | ((p as u32) << shift));
    }
  }
}

// --- Именованные пины, используемые в проекте ---

// Port C: SPI0
pub const PC2: Pin = Pin::new(Port::C, 2); // SPI0-CLK
pub const PC3: Pin = Pin::new(Port::C, 3); // SPI0-CS  (ручной GPIO)
pub const PC4: Pin = Pin::new(Port::C, 4); // SPI0-MOSI
pub const PC5: Pin = Pin::new(Port::C, 5); // SPI0-MISO

// Port D: SPI1 / DBI
pub const PD10: Pin = Pin::new(Port::D, 10); // SPI1-CS
pub const PD11: Pin = Pin::new(Port::D, 11); // SPI1-CLK
pub const PD12: Pin = Pin::new(Port::D, 12); // SPI1-MOSI
pub const PD13: Pin = Pin::new(Port::D, 13); // SPI1-MISO

// Port E: UART0
pub const PE2: Pin = Pin::new(Port::E, 2); // UART0-TX
pub const PE3: Pin = Pin::new(Port::E, 3); // UART0-RX
