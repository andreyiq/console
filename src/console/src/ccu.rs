//! CCU (Clock Control Unit) для F133. Тактирование и сброс периферии.
//! Раздел 3.3.6 user manual.
//!
//! Высокоуровневый API: `Peripheral::Spi0.enable()`.
//! Добавить новую периферию = добавить вариант в enum + match arm в `info()`.

use core::ptr::{read_volatile, write_volatile};

pub const CCU_BASE: u32 = 0x0200_1000;

// --- Низкоуровневые адреса регистров (для обучения и отладки) ---
pub const UART_BGR_REG: *mut u32 = (CCU_BASE + 0x090C) as *mut u32;
pub const SPI0_CLK_REG: *mut u32 = (CCU_BASE + 0x0940) as *mut u32;
pub const SPI1_CLK_REG: *mut u32 = (CCU_BASE + 0x0944) as *mut u32;
pub const SPI_BGR_REG: *mut u32 = (CCU_BASE + 0x096C) as *mut u32;
pub const DMA_BGR_REG: *mut u32 = (CCU_BASE + 0x070C) as *mut u32; // 3.3.6.46 — такт DMAC от PSI_CLK

/// Bit 31 в CLK_REG — одинаков для всех периферий с отдельным тактом.
const CLK_GATING_BIT: u32 = 1 << 31;

/// Периферия F133, управляемая через CCU.
pub enum Peripheral {
  Spi0,
  Spi1,
  Uart0,
  Uart1,
  Dmac,
  // будущие: I2c0, I2c1, Smhc0, ...
}

/// Описание периферии: где её такт, где её reset/gating, и биты в BGR_REG.
struct PeripheralInfo {
  /// CLK_REG, если у периферии есть отдельный тактовый регистр (None у UART).
  clk_reg: Option<*mut u32>,
  /// BGR_REG — общий для группы (SPI0+SPI1 делят SPI_BGR_REG, UART0+UART1 делят UART_BGR_REG).
  bgr_reg: *mut u32,
  /// Бит reset в BGR_REG (0 = assert, 1 = de-assert).
  rst_bit: u32,
  /// Бит gating в BGR_REG (0 = mask, 1 = pass).
  gating_bit: u32,
}

impl Peripheral {
  /// Возвращает описание периферии (адреса и биты). const fn — вычисляется в compile-time.
  const fn info(&self) -> PeripheralInfo {
    match self {
      Peripheral::Spi0 => PeripheralInfo {
        clk_reg: Some(SPI0_CLK_REG),
        bgr_reg: SPI_BGR_REG,
        rst_bit: 1 << 16,
        gating_bit: 1 << 0,
      },
      Peripheral::Spi1 => PeripheralInfo {
        clk_reg: Some(SPI1_CLK_REG),
        bgr_reg: SPI_BGR_REG,
        rst_bit: 1 << 17,
        gating_bit: 1 << 1,
      },
      Peripheral::Uart0 => PeripheralInfo {
        clk_reg: None,
        bgr_reg: UART_BGR_REG,
        rst_bit: 1 << 16,
        gating_bit: 1 << 0,
      },
      Peripheral::Uart1 => PeripheralInfo {
        clk_reg: None,
        bgr_reg: UART_BGR_REG,
        rst_bit: 1 << 17,
        gating_bit: 1 << 1,
      },
      Peripheral::Dmac => PeripheralInfo {
        clk_reg: None,
        bgr_reg: DMA_BGR_REG,
        rst_bit: 1 << 16,
        gating_bit: 1 << 0,
      },
    }
  }

  /// Включить тактовый сигнал (если есть CLK_REG) + снять reset + пропустить шину.
  pub fn enable(&self) {
    let info = self.info();
    unsafe {
      if let Some(clk_reg) = info.clk_reg {
        let v = read_volatile(clk_reg);
        write_volatile(clk_reg, v | CLK_GATING_BIT);
      }
      let v = read_volatile(info.bgr_reg);
      write_volatile(info.bgr_reg, v | info.rst_bit | info.gating_bit);
    }
  }

  /// Текущее значение CLK_REG (None у периферий без отдельного такта, например UART).
  pub fn read_clk(&self) -> Option<u32> {
    unsafe { self.info().clk_reg.map(|r| read_volatile(r)) }
  }

  /// Текущее значение BGR_REG.
  pub fn read_bgr(&self) -> u32 {
    unsafe { read_volatile(self.info().bgr_reg) }
  }
}
