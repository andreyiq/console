//! CCU (Clock Control Unit) для F133. Тактирование и сброс периферии.
//! Раздел 3.3.6 user manual.

use core::ptr::{read_volatile, write_volatile};

pub const CCU_BASE: u32 = 0x0200_1000;

// --- UART0 (раздел 3.3.6.50, 3.3.6.51) ---
pub const UART_BGR_REG: *mut u32 = (CCU_BASE + 0x090C) as *mut u32;
pub const UART0_RST: u32 = 1 << 16;
pub const UART0_GATING: u32 = 1;

// --- SPI0 (раздел 3.3.6.61, 3.3.6.63) ---
pub const SPI0_CLK_REG: *mut u32 = (CCU_BASE + 0x0940) as *mut u32;
pub const SPI0_BGR_REG: *mut u32 = (CCU_BASE + 0x096C) as *mut u32;

pub const SPI0_CLK_GATING: u32 = 1 << 31;
pub const SPI0_RST: u32 = 1 << 16;
pub const SPI0_GATING: u32 = 1;

/// Включает тактовый сигнал SPI0 (bit 31 в SPI0_CLK_REG) и снимает reset
/// + пропускает шину (биты 16 и 0 в SPI_BGR_REG). Источник такта — HOSC
/// (24 MHz) без делителей, как в spi0.sh.
pub fn enable_spi0() {
  unsafe {
    let clk = read_volatile(SPI0_CLK_REG);
    write_volatile(SPI0_CLK_REG, clk | SPI0_CLK_GATING);
    let bgr = read_volatile(SPI0_BGR_REG);
    write_volatile(SPI0_BGR_REG, bgr | SPI0_RST | SPI0_GATING);
  }
}
