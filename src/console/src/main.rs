#![no_std]
#![no_main]

extern crate alloc;

#[macro_use]
pub mod utils;
pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod heap;
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

  // Отладка: текущая конфигурация CPU clock (перед любыми изменениями).
  println!(
    "ccu: pll_cpu=0x{:08x} riscv_clk=0x{:08x} cpu_axi=0x{:08x} pll_peri=0x{:08x}",
    ccu::read_pll_cpu(),
    ccu::read_riscv_clk(),
    ccu::read_cpu_axi(),
    ccu::read_pll_peri()
  );

  // Оптимизация #1: гарантировать CPU @ 1008 МГц (если xfel не настроил).
  ccu::ensure_cpu_clock_1008mhz();
  println!(
    "ccu: after ensure: pll_cpu=0x{:08x} riscv_clk=0x{:08x}",
    ccu::read_pll_cpu(),
    ccu::read_riscv_clk()
  );

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
  // Калибровка CPU clock: 460800 байт при 20 МГц SPI = 460800*8/20e6 = 0.18432 с.
  // Измеряем mcycle вокруг flush → cycles / 0.18432 = реальная частота CPU.
  let cal0 = nes::cycles();
  display.flush_buffer_dma(fb::raw());
  let cal1 = nes::cycles();
  let cpf = cal1 - cal0;
  // cpu_hz = cpf / 0.18432 ≈ cpf * 5.425. Выводим cpf и оценку частоты.
  let cpu_hz_est = cpf * 1000 / 184;
  println!("border flushed (cal: cpf={} cpu_hz_est={}M)", cpf, cpu_hz_est / 1_000_000);

  // Этап 7: Klaus Dormann 6502 functional test — проверка CPU эмулятора.
  let mut nes = nes::smol::Nes::new(&display);
  nes.load_klaus_test(nes::smol::KLAUS_BIN);
  nes.run_klaus_test();
}
