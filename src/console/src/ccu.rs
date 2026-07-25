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
pub const PLL_PERI_CTRL_REG: *mut u32 = (CCU_BASE + 0x0020) as *mut u32; // 3.3.6.3 — PLL_PERI

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

/// Прочитать PLL_PERI_CTRL_REG (для отладки — бит 31 = PLL_EN, бит 28 = LOCK).
pub fn read_pll_peri() -> u32 {
  unsafe { read_volatile(PLL_PERI_CTRL_REG) }
}

/// Включить PLL_PERI если он выключен, и подождать лок (бит 28).
/// PLL_PERI(1X)=600 МГц, (2X)=1.2 ГГц — источник для SPI/SMHC и др.
pub fn ensure_pll_peri_enabled() {
  unsafe {
    let v = read_volatile(PLL_PERI_CTRL_REG);
    if v & (1 << 31) == 0 {
      // PLL_EN (31) + LOCK_ENABLE (29) — чтобы LOCK (28) asserts
      write_volatile(PLL_PERI_CTRL_REG, v | (1 << 31) | (1 << 29));
    }
    // Ждём LOCK с таймаутом (~PLL стабилизируется за <1 мс).
    for _ in 0..10_000_000u32 {
      if read_volatile(PLL_PERI_CTRL_REG) & (1 << 28) != 0 {
        return;
      }
      core::hint::spin_loop();
    }
  }
}

/// Задать такт SPI0 от PLL_PERI(1X) = 600 МГц.
///
/// Модульный такт = 600 / (N × M). Дальше сам контроллер SPI делит его ещё
/// на 2 (`spi.rs`, `CLK_CTL`: DRS=1 + CDR2=0 → /2). То есть SCLK = 300/M МГц
/// при N=1. Тут ставим N=1 и передаём M.
///
/// | M  | модуль  | SCLK     | 184 КБ кадра | примечание                  |
/// |----|---------|----------|--------------|-----------------------------|
/// | 15 | 40 МГц  | 20 МГц   | 73.7 мс      | штатный максимум ILI9488    |
/// | 8  | 75 МГц  | 37.5 МГц | 39.3 мс      | разгон ×1.875               |
/// | 6  | 100 МГц | 50 МГц   | 29.5 мс      | разгон ×2.5                 |
///
/// Про разгон: datasheet ILI9488 даёт минимум 50 нс на цикл записи, то есть
/// 20 МГц — это штатный предел. Выше модули обычно работают, но гарантий нет,
/// и отказ будет не честным, а в виде битых пикселей. Проверять анализатором
/// на PC2.
///
/// `m` — делитель M, допустимо 1..=16 (в регистр пишется `m - 1`).
pub fn set_spi0_clock_pllperi(m: u32) {
  debug_assert!(m >= 1 && m <= 16);
  ensure_pll_peri_enabled();
  // bit31=gate ON | bits26:24=001 (PLL_PERI 1X) | bits9:8=00 (N=1) | bits3:0=M-1
  unsafe {
    write_volatile(SPI0_CLK_REG, 0x8100_0000 | ((m - 1) & 0xF));
  }
}
