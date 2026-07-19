#![no_std]
#![no_main]

#[macro_use]
pub mod utils;
pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod nes;
pub mod spi;
pub mod uart;

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
  spi0.enable();
  // Поднимаем SPI SCLK до ~20 МГц (PLL_PERI 600М / 15 / 1 = 40М модуль, /2 = 20М SCLK).
  ccu::set_spi0_clock_pllperi_20mhz();
  println!(
    "ccu ok: pll_peri=0x{:08x} spi0_clk=0x{:08x}",
    ccu::read_pll_peri(),
    spi0.read_clk().unwrap_or(0)
  );

  // Этап 3: инициализируем SPI0-контроллер.
  let spi = spi::Spi::Spi0;
  spi.init();
  println!("spi0 init ok");

  // Этап 6: включаем DMAC (один раз).
  let dma = dma::Dma::Channel0;
  dma.init();
  println!("dma init ok");

  // Этап 5.5: framebuffer в RAM. Рисуем в массив, потом один flush на экран.
  // DCX на PE0, RESX на PE1.
  let display = display::Display::new(spi, gpio::PE0, gpio::PE1);
  display.init();
  println!("display init ok");

  // Чистый чёрный фон вокруг NES-кадра (256×240 в центре 480×320).
  fb::clear(0x00, 0x00, 0x00);

  // Этап 7: NES-эмулятор. Не возвращается.
  nes::run(&display);
}
