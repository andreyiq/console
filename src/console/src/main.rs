#![no_std]
#![no_main]

pub mod ccu;
pub mod gpio;
pub mod spi;
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

  // Этап 2: включаем тактирование SPI0 через CCU.
  let spi0 = ccu::Peripheral::Spi0;
  let clk_before = spi0.read_clk().unwrap_or(0);
  let bgr_before = spi0.read_bgr();
  spi0.enable();
  let clk_after = spi0.read_clk().unwrap_or(0);
  let bgr_after = spi0.read_bgr();

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

  // Этап 3: инициализируем SPI0-контроллер и отправляем тестовые байты.
  // На анализаторе (PC2 = CLK, PC4 = MOSI) должно быть видно 10 пакетов по 8 тактов.
  let spi = spi::Spi::Spi0;
  spi.init();
  println!("spi0 init ok");

  spi.cs_low();
  for _ in 0..1000_000_0 {
    spi.send_byte(0x55); // 01010101 — хорошо видно на анализаторе
    utils::delay(100_000);
  }
  spi.cs_high();
  println!("spi0 test done");

  loop {
    utils::delay(10_000_000);
  }
}
