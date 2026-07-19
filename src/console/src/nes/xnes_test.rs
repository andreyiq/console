//! Минимальный тест скорости эмулятора x-nes (без рендеринга на экран).
//!
//! Запускает `tick()` в цикле, считает кадры по `bus.ppu.frame_complete`.
//! FPS логируется в UART каждые 10 кадров через mcycle. Никакого вывода
//! на дисплей — чистая скорость эмуляции CPU+PPU+APU.

extern crate alloc;

use xnes::bus::Bus;
use xnes::cpu::CpuRp2a03;
use xnes::rom::Rom;
use xnes::{reset, tick};

use crate::nes::cart;

/// Прочитать mcycle CSR (RISC-V cycle counter).
fn mcycle() -> u64 {
  riscv::register::mcycle::read() as u64
}

/// Запустить x-nes speed test. Не возвращается.
pub fn run() -> ! {
  // ROM уже вшит в прошивку через include_bytes! в cart.rs.
  let data = cart::ROM;
  let rom = match Rom::new(data) {
    Some(r) => r,
    None => {
      println!("xnes: invalid iNES ROM");
      loop {}
    }
  };
  println!(
    "xnes: rom ok mapper={} prg={}B chr={}B",
    rom.mapper_id,
    rom.prg.len(),
    rom.chr.len()
  );

  let mapper = rom.create_mapper();
  let mut cpu = CpuRp2a03::new(0);
  let mut bus = Bus::new(mapper);
  reset(&mut cpu, &mut bus);
  println!("xnes: reset ok, starting speed test (no rendering)");

  const CPU_HZ: u64 = 1_009_000_000;
  let mut frame: u32 = 0;
  let mut last_mcycle: u64 = 0;

  loop {
    // tick() выполняет одну инструкцию 6502, возвращает кол-во циклов.
    let _cycles = tick(&mut cpu, &mut bus);

    if bus.ppu.frame_complete {
      bus.ppu.frame_complete = false;
      frame = frame.wrapping_add(1);

      let now = mcycle();
      if last_mcycle != 0 {
        let cpf = now - last_mcycle;
        if cpf > 0 && frame % 10 == 0 {
          let fps = (CPU_HZ / cpf) as u32;
          println!("xnes: frame {} fps={} cpf={}", frame, fps, cpf);
        }
      }
      last_mcycle = now;
    }
  }
}
