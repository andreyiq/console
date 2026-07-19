#![no_std]
#![no_main]

pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
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

/// Прочитать счётчик циклов (RISC-V mcycle CSR). Для замеров времени.
fn cycles() -> u64 {
  riscv::register::mcycle::read() as u64
}

#[riscv_rt::entry]
fn main() -> ! {
  uart::init_uart0();
  utils::delay(100_000);
  println!("hello");

  // Этап 2: включаем тактирование SPI0 через CCU.
  let spi0 = ccu::Peripheral::Spi0;
  spi0.enable();
  println!("ccu ok");

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

  // Чёрный фон.
  fb::clear(0x00, 0x00, 0x00);

  // 8 цветных полос в верхней половине (каждая 60×160 px).
  const BAR_W: u16 = 60;
  const BAR_H: u16 = 160;
  const COLORS: &[(u8, u8, u8)] = &[
    (0xFF, 0xFF, 0xFF), // белый
    (0xFF, 0xFF, 0x00), // жёлтый
    (0x00, 0xFF, 0xFF), // голубой
    (0x00, 0xFF, 0x00), // зелёный
    (0xFF, 0x00, 0xFF), // магента
    (0xFF, 0x00, 0x00), // красный
    (0x00, 0x00, 0xFF), // синий
    (0x00, 0x00, 0x00), // чёрный
  ];
  for (i, &(r, g, b)) in COLORS.iter().enumerate() {
    fb::fill_rect((i as u16) * BAR_W, 0, BAR_W, BAR_H, r, g, b);
  }

  // Контур прямоугольника в нижней половине.
  fb::draw_rect(20, 180, 440, 120, 0xFF, 0xFF, 0xFF);
  // Диагональные линии.
  fb::draw_h_line(20, 250, 440, 0xFF, 0x00, 0x00);
  fb::draw_v_line(240, 180, 120, 0x00, 0xFF, 0x00);
  // Жёлтый пиксель по центру.
  fb::set_pixel(240, 240, 0xFF, 0xFF, 0x00);

  println!("fb draw done, flushing via DMA...");

  // Один flush через DMA — весь буфер на экран. Замеряем время в циклах.
  let t0 = cycles();
  display.flush_buffer_dma(fb::raw());
  let t1 = cycles();
  println!("dma flush done: {} cycles", t1 - t0);

  loop {
    utils::delay(10_000_000);
  }
}
