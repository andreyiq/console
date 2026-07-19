#![no_std]
#![no_main]

pub mod ccu;
pub mod display;
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

  // Этап 5: рисование примитивов — тестовый паттерн.
  // DCX на PE0, RESX на PE1.
  let display = display::Display::new(spi, gpio::PE0, gpio::PE1);
  display.init();
  println!("display init ok");

  // Чёрный фон.
  display.fill_rgb(0x00, 0x00, 0x00);

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
    display.fill_rect((i as u16) * BAR_W, 0, BAR_W, BAR_H, r, g, b);
  }

  // Контур прямоугольника в нижней половине.
  display.draw_rect(20, 180, 440, 120, 0xFF, 0xFF, 0xFF);

  // Диагональные линии (красная и зелёная).
  display.draw_h_line(20, 250, 440, 0xFF, 0x00, 0x00);
  display.draw_v_line(240, 180, 120, 0x00, 0xFF, 0x00);

  // Один пиксель по центру (жёлтый).
  display.draw_pixel(240, 240, 0xFF, 0xFF, 0x00);

  println!("display draw done");

  loop {
    utils::delay(10_000_000);
  }
}
