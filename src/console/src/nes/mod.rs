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
use crate::nes::input::Buttons;
use crate::nes::mapper0::Mapper0;
use crate::nes::screen::{
  clear_flush, flush_needed, FbScreen, NES_H, NES_OFFSET_X, NES_OFFSET_Y, NES_W,
};
use crate::nes::speaker::PwmSpeaker;

/// Прочитать счётчик циклов (RISC-V mcycle CSR). Для замеров времени.
fn cycles() -> u64 {
  riscv::register::mcycle::read() as u64
}

/// Прочитать minstret (CSR 0xb02) — число выполненных инструкций.
///
/// Вместе с `cycles()` даёт IPC. Это ключевая цифра для выбора оптимизации:
/// низкий IPC значит ядро стоит на промахах кэша (лечится раскладкой данных),
/// высокий — мы честно выполняем слишком много инструкций (лечится алгоритмом).
fn instrs() -> u64 {
  let v: u64;
  unsafe {
    core::arch::asm!("csrr {}, 0xb02", out(reg) v, options(nomem, nostack));
  }
  v
}

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

  // Input — три кнопки на PE4/PE5/PE6 (влево, вправо, прыжок) + автонажатие
  // START. Joystick реализует протокол опроса NES; пины читаются раз в кадр из
  // `input::tick_frame`, который двигает главный цикл.
  input::init();
  let p1 = Buttons;
  let p1ctl = stdctl::Joystick::new(&p1);

  // Speaker — заглушка (нет аудиовыхода).
  let mut spk = PwmSpeaker::new();

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

  use crate::utils::CPU_HZ;

  /// Каждые сколько кадров печатать замер в UART. Строка ~45 байт, на 115200
  /// это ~4 мс — на медленном baseline (~1 FPS) погрешность <1%, но при
  /// высоком FPS интервал надо поднимать, иначе UART сам станет боттлнеком.
  const LOG_EVERY: u32 = 10;

  let mut frame: u32 = 0;
  let mut fps: u32 = 0;
  // Начало текущего кадра. Мерим дельту между flush'ами — это и есть время
  // кадра. (Раньше t0 сбрасывался на каждой инструкции, поэтому в FPS
  // попадало время одной инструкции + flush, и цифра была бессмысленно
  // завышенной.)
  let mut t_frame = cycles();
  // Инструкции на начало кадра — для IPC той же области, что и `emu`.
  let mut i_frame = instrs();
  // Начало текущего окна замера (LOG_EVERY кадров). FPS считаем по реальному
  // прошедшему времени, а не по сумме emu+wait: между ними есть ещё оверлей
  // и настройка DMA, они тоже входят в кадр.
  let mut t_bench = t_frame;
  // Разбивка: emu — эмуляция, wait — простой в ожидании прошлого DMA.
  let mut emu_acc: u64 = 0;
  let mut wait_acc: u64 = 0;
  // Инструкции, выполненные в эмуляции (та же область, что `emu_acc`).
  let mut instr_acc: u64 = 0;
  // Идёт ли сейчас неблокирующий перенос кадра на дисплей.
  let mut dma_in_flight = false;
  // Разбивка эмуляции: tick — прокрутка bus.tick(), step — cpu.step().
  //
  // ВНИМАНИЕ: границу CPU/PPU это НЕ разделяет, замерено — `tick=2ms
  // step=24ms` из 26. В runes каждое `mem.read`/`mem.write` внутри
  // инструкции само зовёт `bus.tick()`, поэтому PPU и APU крутятся внутри
  // `cpu.step()`, а leftover-цикл почти всегда пустой. Чтобы понять, сколько
  // стоит именно PPU, нужен другой подход (например ablation: заглушить
  // `FbScreen::put` и сравнить) — снаружи runes это не видно.
  //
  // Стоит ~2 чтения mcycle на инструкцию, ~0.5 мс из 26. Держим выключенным.
  const PROFILE_EMU: bool = false;
  let mut tick_acc: u64 = 0;
  let mut step_acc: u64 = 0;

  loop {
    let t_a = if PROFILE_EMU { cycles() } else { 0 };

    // Докручиваем leftover циклы предыдущей инструкции — bus.tick() гонит
    // PPU (3 тика на CPU-цикл) и APU. PPU в процессе зовёт scr.put().
    while cpu.cycle > 0 {
      cpu.mem.bus.tick();
    }

    let t_b = if PROFILE_EMU { cycles() } else { 0 };

    // Выполняем одну инструкцию 6502.
    cpu.step();

    if PROFILE_EMU {
      let t_c = cycles();
      tick_acc += t_b - t_a;
      step_acc += t_c - t_b;
    }

    // PPU вызовет render() когда кадр готов — atomic-флаг.
    if flush_needed() {
      let t_emu_end = cycles();
      let i_emu_end = instrs();

      // 1. Дождаться прошлого переноса. Это единственное место, где мы
      //    блокируемся: пока шёл SPI, PPU уже отрисовал следующий кадр.
      //    Ждать надо ДО того, как начнём трогать SPI и буфер: `start()`
      //    держит CS низким и шлёт команды, поэтому две передачи разом
      //    невозможны.
      // (false бывает только на самом первом кадре — дальше мы всегда
      //  запускаем новый перенос в конце этого же блока)
      if dma_in_flight {
        display.flush_region_dma_finish();
      }
      let t_wait_end = cycles();

      // 2. FPS поверх готового кадра: чёрная подложка + белые цифры в углу.
      //    Цифры 3×5 шрифтом, масштаб ×2 → 6×10 на цифру. До 3 цифр = 22×10.
      //    Подложка 26×14 с 2px padding.
      //    Пишем в ready-буфер — тот, что сейчас пойдёт в DMA. Другой буфер
      //    уже под PPU, его трогать нельзя.
      let p = screen::ready_ptr();
      screen::fill_rect(p, 2, 2, 26, 14, 0x00, 0x00, 0x00);
      screen::draw_number(p, fps, 4, 4, 2, 0xFF, 0xFF, 0xFF);

      // 3. Запустить перенос и сразу вернуться к эмуляции — CPU считает
      //    кадр N+1, пока DMA гонит кадр N. Время кадра становится
      //    max(emu, flush) вместо emu + flush.
      display.flush_region_dma_start(
        screen::ready_raw(),
        NES_OFFSET_X,
        NES_OFFSET_Y,
        NES_W as u16,
        NES_H as u16,
      );
      dma_in_flight = true;
      clear_flush();

      let t_end = cycles();
      emu_acc += t_emu_end - t_frame;
      wait_acc += t_wait_end - t_emu_end;
      instr_acc += i_emu_end - i_frame;
      t_frame = t_end;
      i_frame = instrs();
      frame = frame.wrapping_add(1);
      input::tick_frame();

      // Каждые LOG_EVERY кадров — лог в UART: средний FPS и разбивка кадра.
      // emu — эмуляция (CPU+PPU+APU), wait — простой в ожидании SPI.
      // Если wait≈0 — боттлнек эмуляция; если wait большой — боттлнек SPI.
      // FPS считаем из суммарного времени, а не из последнего кадра.
      if frame % LOG_EVERY == 0 {
        let total = t_end.wrapping_sub(t_bench);
        let per_ms = CPU_HZ / 1000;
        let emu_ms = emu_acc / (LOG_EVERY as u64) / per_ms;
        let wait_ms = wait_acc / (LOG_EVERY as u64) / per_ms;
        if total > 0 {
          fps = (CPU_HZ * (LOG_EVERY as u64) / total) as u32;
        }
        let tick_ms = tick_acc / (LOG_EVERY as u64) / per_ms;
        let step_ms = step_acc / (LOG_EVERY as u64) / per_ms;
        // emu в микросекундах: разница между режимами ablation — единицы мс,
        // в целых мс её не видно.
        let emu_us = emu_acc / (LOG_EVERY as u64) * 1000 / per_ms;
        // Инструкций на кадр (тысячами) и IPC×100 в области эмуляции.
        let instr_k = instr_acc / (LOG_EVERY as u64) / 1000;
        let ipc100 = if emu_acc > 0 { instr_acc * 100 / emu_acc } else { 0 };
        println!(
          "nes: frame {} fps={} emu={}ms ({}us) wait={}ms instr={}k ipc={} peak={} btn={:02x} (tick={}ms step={}ms)",
          frame,
          fps,
          emu_ms,
          emu_us,
          wait_ms,
          instr_k,
          ipc100,
          speaker::take_peak(),
          input::take_seen(),
          tick_ms,
          step_ms
        );
        emu_acc = 0;
        wait_acc = 0;
        instr_acc = 0;
        tick_acc = 0;
        step_acc = 0;
        t_bench = t_end;
      }
    }
  }
}
