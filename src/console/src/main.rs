#![no_std]
#![no_main]

#[macro_use]
pub mod utils;
pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod heap;
pub mod nesrecomp;
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

  // PRG ROM integrity check в самом начале main (до любой инициализации).
  extern "C" { static g_mario_prg: u8; }
  let prg = core::ptr::addr_of!(g_mario_prg) as *const u8;
  let mut hex = [0u8; 48];
  let mut p = 0;
  for i in 0..16 {
    let b = unsafe { *prg.add(i) };
    let hi = (b >> 4) & 0xF;
    let lo = b & 0xF;
    hex[p] = if hi < 10 { b'0' + hi } else { b'A' + hi - 10 };
    hex[p+1] = if lo < 10 { b'0' + lo } else { b'A' + lo - 10 };
    hex[p+2] = b' ';
    p += 3;
  }
  let hex_str = unsafe { core::str::from_utf8_unchecked(&hex) };
  let rv = unsafe { (*prg.add(0x7FFC) as u16) | ((*prg.add(0x7FFD) as u16) << 8) };
  println!("EARLY PRG[0..16]={} reset_vec=0x{:04x}", hex_str, rv);
  // Длинная задержка чтобы UART успел вывести до потока других сообщений.
  utils::delay(3_000_000);

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

  // Чистый чёрный фон на весь экран — один полный flush, чтобы задать
  // чёрную рамку вокруг NES-кадра. Дальше NES будет делать partial flush
  // только области 256×240 (184 KB вместо 460 KB).
  fb::clear(0x00, 0x00, 0x00);
  display.flush_buffer_dma(fb::raw());
  println!("border flushed");

  // Этап 7: nesrecomp — статически рекомпилированный mario.
  // RESET не возвращается — это главный цикл NES. Каждый VBlank C runner
  // рендерит кадр и вызывает nesrecomp_on_frame → flush на дисплей.
  // Display живёт вечно (main не возвращается) — продлеваем lifetime до 'static.
  let display_ref: &'static display::Display = unsafe { &*(&display as *const display::Display) };
  nesrecomp::init(display_ref);
  nesrecomp::run();
}
