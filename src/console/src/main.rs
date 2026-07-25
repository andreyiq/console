#![no_std]
#![no_main]

#[macro_use]
pub mod utils;
pub mod cache;
pub mod ccu;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod nes;
pub mod pwm;
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

  // Кэши C906. Ядро стартует с MHCR=0 (кэши выключены), boot0 мы пропускаем
  // через `xfel exec`, а riscv-rt вендорские CSR не трогает. Печатаем «до» и
  // «после», чтобы видеть, что реально было выключено.
  println!(
    "cache before: mhcr=0x{:x} mxstatus=0x{:x} mhint=0x{:x}",
    cache::read_mhcr(),
    cache::read_mxstatus(),
    cache::read_mhint()
  );
  cache::enable();
  println!(
    "cache after:  mhcr=0x{:x} mxstatus=0x{:x} mhint=0x{:x}",
    cache::read_mhcr(),
    cache::read_mxstatus(),
    cache::read_mhint()
  );

  // Этап 2: включаем тактирование SPI0 через CCU.
  let spi0 = ccu::Peripheral::Spi0;
  spi0.enable();
  // SPI SCLK. M=15 → 20 МГц (штатный максимум ILI9488, 73.7 мс на кадр),
  // M=8 → 37.5 МГц (39.3 мс), M=6 → 50 МГц (29.5 мс). См. таблицу в ccu.rs.
  // 50 МГц — разгон ×2.5 относительно datasheet, проверяем на глаз.
  ccu::set_spi0_clock_pllperi(6);
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

  // Этап 12: звук. Проверочный писк до старта эмулятора — если его слышно,
  // цепочка PWM7 → PD22 → резистор → динамик рабочая, и дальше уже вопрос
  // только к сэмплам APU. Ля первой октавы, 300 мс.
  pwm::init();
  let (pccr, pcgr, per, ppr) = pwm::read_regs();
  println!(
    "pwm init: pccr67=0x{:08x} pcgr=0x{:08x} per=0x{:08x} ppr=0x{:08x}",
    pccr, pcgr, per, ppr
  );
  // Сначала глазами: 5 миганий синего DS2 (он висит на PD22 через 5.1К).
  // Если диод мигает — PWM7 доходит до ножки, и вопрос только в акустике.
  println!("pwm: blinking DS2 (blue led) 5 times...");
  pwm::led_test(5);
  // Потом ушами: ля первой октавы, секунда, полный размах.
  println!("pwm: beep 440 Hz...");
  pwm::beep(440, 1000);
  println!("beep done");

  // Этап 7: NES-эмулятор. Не возвращается.
  nes::run(&display);
}
