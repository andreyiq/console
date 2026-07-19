#![no_std]
#![no_main]

#[macro_use]
pub mod utils;
pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod spi;
pub mod uart;

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

  // Главный цикл: каждый кадр рисуем сцену + FPS, flush через DMA, замеряем время.
  // CPU C906 на F133 ~1 ГГц → FPS = 1_000_000_000 / циклов_за_кадр.
  const CPU_HZ: u64 = 1_000_000_000;
  let mut fps: u32 = 0;
  let mut frame: u32 = 0;
  loop {
    let t0 = cycles();

    // Сцена: чёрный фон + 8 цветных полос + движущийся красный столб.
    fb::clear(0x00, 0x00, 0x00);
    const BAR_W: u16 = 60;
    const BAR_H: u16 = 160;
    const COLORS: &[(u8, u8, u8)] = &[
      (0xFF, 0xFF, 0xFF),
      (0xFF, 0xFF, 0x00),
      (0x00, 0xFF, 0xFF),
      (0x00, 0xFF, 0x00),
      (0xFF, 0x00, 0xFF),
      (0xFF, 0x00, 0x00),
      (0x00, 0x00, 0xFF),
      (0x00, 0x00, 0x00),
    ];
    for (i, &(r, g, b)) in COLORS.iter().enumerate() {
      fb::fill_rect((i as u16) * BAR_W, 0, BAR_W, BAR_H, r, g, b);
    }
    // Движущийся красный столб — видно обновление глазами.
    let bx = ((frame * 8) % 480) as u16;
    fb::fill_rect(bx, 170, 20, 140, 0xFF, 0x00, 0x00);

    // FPS: чёрная подложка поверх полос + белые цифры (иначе белое по белому не видно).
    fb::fill_rect(5, 5, 90, 30, 0x00, 0x00, 0x00);
    fb::draw_number(fps, 10, 10, 4, 0xFF, 0xFF, 0xFF);

    display.flush_buffer_dma(fb::raw());

    let t1 = cycles();
    let cpf = t1 - t0;
    if cpf > 0 {
      fps = (CPU_HZ / cpf) as u32;
    }
    frame = frame.wrapping_add(1);

    // Раз в 30 кадров — дублируем FPS в UART (для контроля, если на экране не видно).
    if frame % 30 == 0 {
      println!("fps={} cpf={}", fps, cpf);
    }
  }
}
