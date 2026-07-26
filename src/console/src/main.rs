#![no_std]
#![no_main]

#[macro_use]
pub mod utils;
pub mod cache;
pub mod ccu;
pub mod codec;
pub mod display;
pub mod dma;
pub mod fb;
pub mod gpio;
pub mod nes;
pub mod pwm;
pub mod spi;
pub mod tone;
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

  // Этап 12: PWM7 на PD22. Как аудиовыход больше не используется (звук ушёл
  // на кодек, см. ниже), но пин остался: на нём висит синий DS2 через 5.1К, и
  // мигание — самая быстрая проверка, что до ножки вообще доходит сигнал.
  pwm::init();
  let (pccr, pcgr, per, ppr) = pwm::read_regs();
  println!(
    "pwm init: pccr67=0x{:08x} pcgr=0x{:08x} per=0x{:08x} ppr=0x{:08x}",
    pccr, pcgr, per, ppr
  );
  println!("pwm: blinking DS2 (blue led) 5 times...");
  pwm::led_test(5);

  // Этап 14: аудиокодек. Настоящий 16-битный ЦАП вместо PWM: HPOUTL/R →
  // развязка C54/C15 → PAM8301 (U10) на плате → гребёнка P6 «AUDIO», куда
  // припаян динамик. Печатаем регистры — по ним видно, поднялся ли PLL_AUDIO1
  // (бит 28 = LOCK) и включился ли выходной каскад.
  codec::init();
  let (dpc, fifoc, fifos, dac, ramp, hp1, hp2, power, pll, clk) = codec::read_regs();
  println!(
    "codec: dpc=0x{:08x} fifoc=0x{:08x} fifos=0x{:08x} dac=0x{:08x}",
    dpc, fifoc, fifos, dac
  );
  println!(
    "codec: ramp=0x{:08x} hp1=0x{:08x} hp2=0x{:08x} power=0x{:08x} pll_audio1=0x{:08x} dac_clk=0x{:08x}",
    ramp, hp1, hp2, power, pll, clk
  );
  // Забирает ли ЦАП сэмплы. Ждём ~24000; ноль означает, что цифровая часть
  // стоит, и искать надо в тактировании, а не в аналоге.
  println!("codec: fifo drain rate = {} Hz (ожидаем ~24000)", codec::measure_rate());
  // Тестовых сигналов при загрузке нет: слушать их на каждой прошивке
  // невыносимо. Когда нужно проверить тракт до эмулятора, раскомментируй —
  // сетку отсчётов там держит FIFO кодека, то есть дрожания вывода нет по
  // построению, и если там чисто, а в игре нет, виновата выдача сэмплов APU.
  //
  //   tone::quality_test();   // свип + фортепиано: оценка динамика
  //   tone::level_test();     // 1 кГц на 0 / -9 / -18 дБ: ищем перегрузку
  //   tone::compare_waves();  // синус / меандр / импульс: акустика или код
  //   tone::melody();         // ноты: только «звук есть, высота верная»

  // Этап 7: NES-эмулятор. Не возвращается.
  nes::run(&display);
}
