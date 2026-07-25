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
/// ВАЖНО: варианты `Spi0`/`Spi1`/`Uart0`/`Pwm7` привязаны к конкретным пинам:
///   `Spi0`  → PC (func=0b0010)
///   `Spi1`  → PD (func=0b0100)
///   `Uart0` → PE (func=0b0110)
///   `Pwm7`  → PD22 (func=0b0101, таблица PD22_SELECT в user manual)
/// Передача «не своего» варианта в пин другого порта скомпилируется,
/// но настроит пин на чужую функцию (см. таблицу 9.7 в user manual).
#[derive(Clone, Copy)]
#[repr(u32)]
pub enum Func {
  Input = 0b0000,
  Output = 0b0001,
  Spi0 = 0b0010,
  Spi1 = 0b0100,
  Pwm7 = 0b0101,
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

  /// Адрес нужного CFG-регистра. По 4 бита на пин, значит в один 32-битный
  /// регистр влезает 8 пинов: CFG0 — пины 0..7, CFG1 — 8..15 (+0x04),
  /// CFG2 — 16..23 (+0x08), CFG3 — 24..31 (+0x0C).
  fn cfg_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset() + (self.pin / 8) * 4) as *mut u32
  }

  fn dat_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset() + 0x10) as *mut u32
  }

  /// Адрес нужного PULL-регистра. По 2 бита на пин: PULL0 — пины 0..15,
  /// PULL1 — 16..31 (+0x04).
  fn pull_addr(&self) -> *mut u32 {
    (PIO_BASE + self.cfg0_offset() + 0x24 + (self.pin / 16) * 4) as *mut u32
  }

  /// Установить функцию пина. Внутри своего CFG-регистра пин занимает
  /// биты [4k+3 : 4k], где k = pin % 8.
  #[inline]
  pub fn set_func(&self, f: Func) {
    let shift = (self.pin % 8) * 4;
    let mask = 0xF << shift;
    unsafe {
      let old = read_volatile(self.cfg_addr());
      write_volatile(self.cfg_addr(), (old & !mask) | ((f as u32) << shift));
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

  /// Прочитать уровень на пине: `true` = 0 (низкий).
  ///
  /// Имеет смысл только для пина, настроенного на `Func::Input`. Кнопки у нас
  /// замкнуты на GND и подтянуты вверх (`Pull::Up`), так что нажатая = low.
  #[inline]
  pub fn is_low(&self) -> bool {
    unsafe { read_volatile(self.dat_addr()) & (1u32 << self.pin) == 0 }
  }

  /// Прочитать CFG-регистр пина целиком (отладка: проверить, что set_func лёг).
  pub fn read_cfg(&self) -> u32 {
    unsafe { read_volatile(self.cfg_addr()) }
  }

  /// Прочитать PULL-регистр пина целиком (отладка: проверить set_pull).
  pub fn read_pull(&self) -> u32 {
    unsafe { read_volatile(self.pull_addr()) }
  }

  /// Прочитать DAT-регистр порта целиком (отладка: уровни всех пинов сразу).
  pub fn read_dat(&self) -> u32 {
    unsafe { read_volatile(self.dat_addr()) }
  }

  /// Установить pull-up/down. Внутри своего PULL-регистра пин занимает
  /// биты [2k+1 : 2k], где k = pin % 16.
  #[inline]
  pub fn set_pull(&self, p: Pull) {
    let shift = (self.pin % 16) * 2;
    let mask = 0b11 << shift;
    unsafe {
      let old = read_volatile(self.pull_addr());
      write_volatile(self.pull_addr(), (old & !mask) | ((p as u32) << shift));
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
pub const PD22: Pin = Pin::new(Port::D, 22); // PWM7 (звук), гребёнка P2 пин 19

// Port E: UART0 (PE2/PE3) + ILI9488 DC/RST (PE0/PE1) + кнопки (PE4..PE6)
pub const PE0: Pin = Pin::new(Port::E, 0); // ILI9488 DCX (Data/Command)
pub const PE1: Pin = Pin::new(Port::E, 1); // ILI9488 RESX (Reset)
pub const PE2: Pin = Pin::new(Port::E, 2); // UART0-TX
pub const PE3: Pin = Pin::new(Port::E, 3); // UART0-RX
// Кнопки на гребёнке P3, сразу за PE2/PE3. Замкнуты на GND, подтянуты вверх.
pub const PE4: Pin = Pin::new(Port::E, 4); // кнопка LEFT
pub const PE5: Pin = Pin::new(Port::E, 5); // кнопка RIGHT
pub const PE6: Pin = Pin::new(Port::E, 6); // кнопка A (прыжок)
