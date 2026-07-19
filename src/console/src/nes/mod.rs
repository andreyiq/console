//! NES-эмулятор на базе крейта `runes`.
//!
//! Главная точка входа — `run(&display)`. Она парсит iNES-заголовок из
//! встроенного ROM, строит картридж + маппер, собирает машину
//! (CPU + PPU + APU + Screen + Input) и запускает эмуляционный цикл.
//!
//! Маппер выбирается по `mapper_id` из заголовка. Поскольку `runes` хранит
//! ссылки с lifetime, а разные мапперы — разные типы, мы не можем положить
//! их в одну переменную без `Box` (аллокатора нет). Решение — generic-функция
//! `run_with_mapper::<M>`, которая мономорфизируется под каждый маппер.
//!
//! Поддержанные мапперы (через `runes`):
//!   0 (NROM)  → эмулируем через Mapper2 (с 1 PRG-банком работает идентично NROM-128)
//!   1 (MMC1)  → `runes::mapper::Mapper1`
//!   2 (UxROM) → `runes::mapper::Mapper2`
//!   4 (MMC3)  → `runes::mapper::Mapper4`

pub mod cart;
pub mod input;
pub mod mapper0;
pub mod palette;
pub mod screen;
pub mod speaker;

use runes::apu::APU;
use runes::controller::stdctl;
use runes::mapper::{self, Mapper, RefMapper};
use runes::memory::{CPUMemory, PPUMemory};
use runes::mos6502;
use runes::ppu;

use crate::display::Display;
use crate::fb;
use crate::nes::input::NoInput;
use crate::nes::mapper0::Mapper0;
use crate::nes::screen::{clear_flush, flush_needed, FbScreen};
use crate::nes::speaker::NoAudio;

/// Запустить NES-эмулятор. Инициализация железа (UART/CCU/SPI/DMA/Display)
/// уже должна быть выполнена вызывающим. Функция не возвращается.
pub fn run(display: &Display) -> ! {
  let info = cart::parse_header(cart::ROM);
  println!(
    "nes: rom={} mapper={} prg={}B chr={}B mirror={}",
    cart::ROM_NAME,
    info.mapper_id,
    info.prg_len,
    info.chr_len,
    info.mirror as u8
  );

  // SRAM — берём один раз из static mut.
  let sram = cart::take_sram();
  let cart_obj = cart::StaticCart::new(
    cart::ROM,
    info.chr_pos,
    info.chr_len,
    info.prg_pos,
    info.prg_len,
    sram,
    info.mirror,
  );
  println!("nes: cart built, rom_len={}", cart::ROM.len());

  // Выбор маппера. Каждый arm вызывает run_with_mapper с конкретным типом.
  match info.mapper_id {
    0 => run_with_mapper(Mapper0::new(cart_obj), display),
    2 => run_with_mapper(mapper::Mapper2::new(cart_obj), display),
    1 => run_with_mapper(mapper::Mapper1::new(cart_obj), display),
    4 => run_with_mapper(mapper::Mapper4::new(cart_obj), display),
    id => panic!("unsupported mapper {}", id),
  }
}

/// Общий эмуляционный цикл, параметризованный типом маппера.
/// Мономорфизируется под каждый маппер — никакого `Box<dyn Mapper>`.
fn run_with_mapper<M: Mapper>(mut m: M, display: &Display) -> ! {
  // RefMapper оборачивает `&mut dyn Mapper` чтобы его можно было шарить
  // между CPU и PPU (через UnsafeCell внутри).
  let mapper = RefMapper::new(&mut m as &mut dyn Mapper);

  // Input — заглушка (нет кнопок). Joystick обёртка реализует протокол опроса NES.
  let p1 = NoInput;
  let p1ctl = stdctl::Joystick::new(&p1);

  // Speaker — заглушка (нет аудиовыхода).
  let mut spk = NoAudio;

  // Screen — наш framebuffer в центре 480×320.
  let mut scr = FbScreen::new();

  // Собираем машину: CPU + PPU + APU.
  let mut cpu = mos6502::CPU::new(CPUMemory::new(&mapper, Some(&p1ctl), None));
  let mut ppu = ppu::PPU::new(PPUMemory::new(&mapper), &mut scr);
  let mut apu = APU::new(&mut spk);

  // Связываем CPU/PPU/APU через bus: bus хранит raw-указатели на них,
  // чтобы любой из них мог дёрнуть остальные во время tick().
  let cpu_ptr = &mut cpu as *mut mos6502::CPU;
  cpu.mem.bus.attach(cpu_ptr, &mut ppu, &mut apu);

  cpu.powerup();
  println!("nes: powerup ok, starting loop");

  let mut frame: u32 = 0;
  loop {
    // Докручиваем leftover циклы предыдущей инструкции — bus.tick() гонит
    // PPU (3 тика на CPU-цикл) и APU. PPU в процессе зовёт scr.put().
    while cpu.cycle > 0 {
      cpu.mem.bus.tick();
    }
    // Выполняем одну инструкцию 6502 — она выставит cpu.cycle для следующего шага.
    cpu.step();

    // PPU вызовет render() когда кадр готов — мы ставим atomic-флаг.
    // Здесь проверяем флаг и flush-им framebuffer на дисплей через DMA.
    if flush_needed() {
      display.flush_buffer_dma(fb::raw());
      clear_flush();
      frame = frame.wrapping_add(1);
      // Раз в 60 кадров — лог в UART (для контроля FPS).
      if frame % 60 == 0 {
        println!("nes: frame {}", frame);
      }
    }
  }
}
