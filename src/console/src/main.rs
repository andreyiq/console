#![no_std]
#![no_main]

pub mod ccu;
pub mod gpio;
pub mod uart;
#[macro_use]
pub mod utils;

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
  loop {}
}

#[riscv_rt::entry]
fn main() -> ! {
  uart::init_uart0();
  utils::delay(100_000);
  println!("hello");

  // Этап 2: включаем тактирование и снимаем reset для SPI0.
  // Печатаем значения регистров до/после — убеждаемся что биты выставились.
  let clk_before = ccu::read_spi0_clk();
  let bgr_before = ccu::read_spi0_bgr();
  ccu::enable_spi0();
  let clk_after = ccu::read_spi0_clk();
  let bgr_after = ccu::read_spi0_bgr();

  print!("SPI0_CLK before=");
  utils::print_hex(clk_before);
  print!(" after=");
  utils::print_hex(clk_after);
  println!("");

  print!("SPI0_BGR before=");
  utils::print_hex(bgr_before);
  print!(" after=");
  utils::print_hex(bgr_after);
  println!("");

  // Этап 1: мигаем PC2 (SPI0-CLK пин, сейчас как GPIO output).
  // На логическом анализаторе должно быть видно квадратную волну.
  gpio::pc_set_func(gpio::PinC::P2, gpio::Func::Output);

  loop {
    gpio::pc_set_high(gpio::PinC::P2);
    utils::delay(1_000_000);
    gpio::pc_set_low(gpio::PinC::P2);
    utils::delay(1_000_000);
  }
}
