//! Bare-metal FFI к nesrecomp (mario, статически рекомпилированный в C).
//!
//! libmario.a содержит:
//!   - сгенерированный mario_full_*.c (recompiled 6502 → C)
//!   - bare_runner.c (g_cpu, g_ram, nes_read/nes_write, dispatch, vblank)
//!   - mapper.c (NROM), ppu.c (scanline renderer), stubs.c, interp.c
//!
//! Точка входа: `init(display)` + `run()`. RESET не возвращается
//! (это главный цикл NES). VBlank/NMI инжектируется через `maybe_trigger_vblank`
//! из generated JMP-инструкций. Каждый кадр C вызывает `nesrecomp_on_frame`
//! с RGB888 buffer 256x240, который мы flush-им на дисплей через partial DMA.

use crate::display::Display;
use core::ptr;

extern "C" {
  fn runtime_init();
  fn func_RESET();
}

/// Прочитать счётчик циклов (RISC-V mcycle CSR). Для замеров времени.
#[inline]
fn cycles() -> u64 {
  riscv::register::mcycle::read() as u64
}

/// Смещение NES-кадра в центре дисплея 480x320.
const OFFSET_X: u16 = (crate::display::WIDTH - 256) / 2; // 112
const OFFSET_Y: u16 = (crate::display::HEIGHT - 240) / 2; // 40

/// Display сохраняется один раз при init, живёт всё время программы.
static mut G_DISPLAY: ptr::NonNull<Display> = ptr::NonNull::dangling();

static mut BENCH_START: u64 = 0;
static mut BENCH_FRAMES: u64 = 0;
/// DMA pipeline: true если предыдущий flush ещё в полёте.
/// В начале on_frame: если true — дождаться завершения (finish),
/// потом запустить новый flush (start, неблокирующий).
/// Это даёт pipeline: ppu рендерит кадр N+1 пока DMA передаёт кадр N.
static mut DMA_IN_FLIGHT: bool = false;

/// Инициализация: сохранить Display для callback'ов из C.
/// Display должен жить всё время работы (main не возвращается).
pub fn init(display: &'static Display) {
  unsafe {
    G_DISPLAY = ptr::NonNull::from(display);
  }
}

/// Trace callback — no-op для максимального FPS.
/// C runtime вызывает её часто, любая работа здесь замедляет.
#[no_mangle]
pub extern "C" fn nesrecomp_trace(_kind: u8, _addr: u16, _val: u8, _frame: u64, _extra: u64) {}

/// dbg_log — no-op.
#[no_mangle]
pub extern "C" fn nesrecomp_dbg_log(_frame: u64, _tag: *const u8, _v0772: u8, _v0774: u8, _v0770: u8, _v073c: u8) {}

/// Callback из C runner — вызывается каждый VBlank (NMI) после рендера кадра.
/// `buf` — RGB888 buffer 256x240 (3 байта/пиксель, 184KB).
/// Pipeline: ждём прошлый DMA → запускаем новый (неблокирующий) → возвращаемся.
/// CPU свободен для рендеринга следующего кадра, пока DMA передаёт этот.
///
/// Замеры (mcycle):
///   between  = t0 - prev_t0  : полный цикл кадра (nmi + ppu + fl + cpu_main)
///   nmi      = t1 - t0       : func_NMI() — CPU эмуляция внутри NMI
///   ppu      = t2 - t1       : ppu_render_frame() — рендеринг PPU
///   flush    = after - before: flush_region_dma_finish() — ожидание прошлого DMA
///   (время start не считаем — оно неблокирующее)
#[no_mangle]
pub extern "C" fn nesrecomp_on_frame(frame: u64, buf: *const u8, w: i32, h: i32) {
  unsafe {
    let len = (w * h * 3) as usize;
    let slice = core::slice::from_raw_parts(buf, len);

    extern "C" {
      fn nesrecomp_get_t0() -> u64;
      fn nesrecomp_get_t1() -> u64;
      fn nesrecomp_get_t2() -> u64;
      fn nesrecomp_get_prev_t0() -> u64;
    }
    let t0      = nesrecomp_get_t0();
    let t1      = nesrecomp_get_t1();
    let t2      = nesrecomp_get_t2();
    let prev_t0 = nesrecomp_get_prev_t0();

    let between = if prev_t0 != 0 { t0.saturating_sub(prev_t0) } else { 0 };
    let nmi     = t1.saturating_sub(t0);
    let ppu     = t2.saturating_sub(t1);

    // Pipeline: сначала ждём прошлый DMA (если был), потом запускаем новый.
    let t_flush_before = cycles();
    if DMA_IN_FLIGHT {
      (*G_DISPLAY.as_ptr()).flush_region_dma_finish();
      DMA_IN_FLIGHT = false;
    }
    let t_flush_after = cycles();
    let flush = t_flush_after.saturating_sub(t_flush_before);

    // Запускаем новый DMA (неблокирующий) — CPU свободен для следующего кадра.
    (*G_DISPLAY.as_ptr()).flush_region_dma_start(slice, OFFSET_X, OFFSET_Y, w as u16, h as u16);
    DMA_IN_FLIGHT = true;

    // Короткий лог каждый кадр (~50 байт) — не перегружает UART.
    println!("f={} bt={} nmi={} ppu={} fl={}", frame, between, nmi, ppu, flush);

    // FPS замер: каждые 60 кадров выводим fps + cpf
    let now = t_flush_after;
    if BENCH_START == 0 {
      BENCH_START = now;
      BENCH_FRAMES = frame;
      return;
    }
    let elapsed = now - BENCH_START;
    if (frame - BENCH_FRAMES) >= 60 {
      let frames = frame - BENCH_FRAMES;
      let cpu_hz: u64 = 1_008_000_000;
      let fps = if elapsed > 0 { frames * cpu_hz / elapsed } else { 0 };
      let cpf = if frames > 0 { elapsed / frames } else { 0 };
      println!("FPS fps={} cpf={} (frames={})", fps, cpf, frames);
      BENCH_START = now;
      BENCH_FRAMES = frame;
    }
  }
}

/// Запуск nesrecomp: инициализация + RESET (никогда не возвращается).
pub fn run() -> ! {
  unsafe {
    runtime_init();
    // На host мы устанавливали Start button чтобы game вышла из title screen.
    // На board пока тоже — stub: Start button нажат.
    extern "C" {
      static mut g_controller1_buttons: u8;
    }
    *core::ptr::addr_of_mut!(g_controller1_buttons) = 0x10; /* bit4 = Start */
    println!("nesrecomp: runtime_init done, starting RESET");
    func_RESET();
    println!("nesrecomp: func_RESET returned (should not happen!)");
    loop {}
  }
}
