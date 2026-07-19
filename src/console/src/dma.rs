//! DMA (Direct Memory Access) для F133. Раздел 3.9 user manual.
//!
//! DMAC переносит данные между памятью и периферией БЕЗ участия CPU.
//! CPU только настраивает канал и запускает; дальше железо само копирует.
//! Для нас это: кадр из RAM-буфера в TX FIFO SPI0 — CPU свободен, SPI не голодает.
//!
//! Модель F133 — **дескрипторная**: конфигурация одной посылки лежит в памяти
//! как 6 слов (24 байта, word-aligned). DMAC читает дескриптор, гонит данные,
//! по ссылке `link` переходит к следующему или останавливается (0xFFFFF800).
//!
//! 6 слов дескриптора:
//!   word 0 — Configuration (формат как у DMAC_CFG_REGN)
//!   word 1 — Source Address (младшие 32 бита 34-битного адреса)
//!   word 2 — Destination Address (младшие 32 бита)
//!   word 3 — Byte Counter (сколько байт перенести)
//!   word 4 — Parameter (старшие 2 бита адресов + доп. параметры)
//!   word 5 — Link (адрес следующего дескриптора, 0xFFFFF800 = конец очереди)
//!
//! Биты Configuration (DMAC_CFG_REGN, 3.9.6.10):
//!   5:0   SRC_DRQ_TYPE       — порт источника (1 = DRAM, 0 = SRAM)
//!   7:6   SRC_BLOCK_SIZE      — 00=1, 01=4, 10=8, 11=16 (байт за один DRQ-такт)
//!   8     SRC_ADDR_MODE       — 0=Linear (адрес растёт), 1=IO (фиксирован)
//!   10:9  SRC_DATA_WIDTH      — 00=8, 01=16, 10=32, 11=64 бит
//!   21:16 DEST_DRQ_TYPE       — порт приёмника (23 = SPI0-TX)
//!   23:22 DEST_BLOCK_SIZE
//!   24    DEST_ADDR_MODE      — 0=Linear, 1=IO (FIFO периферии — фиксирован)
//!   26:25 DEST_DATA_WIDTH
//!   31    BMODE_SEL           — 0=Normal
//!
//! Высокоуровневый API: `Dma::Channel0.start_tx_dram_to_spi0(src, dest, n)`.

use core::ptr::{addr_of_mut, read_volatile, write_volatile};

use crate::ccu;

pub const DMAC_BASE: u32 = 0x0300_2000;

// Канальные регистры: 0x100 + N*0x40. Канал 0:
const EN_REG: u32 = 0x100; // DMAC Channel Enable
const DESC_ADDR_REG: u32 = 0x108; // адрес дескриптора в памяти
const BCNT_LEFT_REG: u32 = 0x118; // сколько байт осталось перенести

// Порты DRQ (3.9.3.3): источник/приёмник.
pub const DRQ_SRAM: u32 = 0;
pub const DRQ_DRAM: u32 = 1;
pub const DRQ_SPI0_TX: u32 = 23;
pub const DRQ_SPI0_RX: u32 = 22;

// Биты Configuration.
const fn cfg(src_drq: u32, dest_drq: u32, dest_io: bool) -> u32 {
  let mut c = 0;
  c |= src_drq & 0x3F; // SRC_DRQ (5:0)
                       // SRC: Linear, 8-bit, block 1 — память, адрес растёт.
  c |= (dest_drq & 0x3F) << 16; // DEST_DRQ (21:16)
  if dest_io {
    c |= 1 << 24; // DEST_ADDR_MODE = IO (FIFO фиксирован)
  }
  // DEST: 8-bit, block 1 — ширина FIFO SPI = 1 байт.
  c
}

/// Конфиг для DRAM → SPI0-TX (8-бит, приёмник IO).
pub const CFG_DRAM_TO_SPI0_TX: u32 = cfg(DRQ_DRAM, DRQ_SPI0_TX, true);
// = (23 << 16) | 1 = 0x01170001

/// Признак конца очереди дескрипторов.
pub const LINK_END: u32 = 0xFFFF_F800;

/// Один дескриптор DMA — 6 слов в памяти (word-aligned).
#[repr(C)]
pub struct Descriptor {
  pub config: u32,
  pub src: u32,
  pub dest: u32,
  pub byte_count: u32,
  pub param: u32,
  pub link: u32,
}

// Место под дескриптор. Лежит в .bss (DRAM), word-aligned благодаря repr(C)+u32.
static mut DESCRIPTOR: Descriptor = Descriptor {
  config: 0,
  src: 0,
  dest: 0,
  byte_count: 0,
  param: 0,
  link: LINK_END,
};

/// Канал DMAC. Сейчас используем только 0-й.
pub enum Dma {
  Channel0,
}

impl Dma {
  const fn base(&self) -> u32 {
    DMAC_BASE
  }

  #[inline]
  fn write_reg(&self, offset: u32, val: u32) {
    unsafe {
      write_volatile((self.base() + offset) as *mut u32, val);
    }
  }

  #[inline]
  fn read_reg(&self, offset: u32) -> u32 {
    unsafe { read_volatile((self.base() + offset) as *const u32) }
  }

  /// Включить тактирование DMAC (через CCU). Вызвать один раз до первого transfer.
  pub fn init(&self) {
    ccu::Peripheral::Dmac.enable();
  }

  /// Запустить перенос `n` байт из `src` (DRAM) в `dest` (фиксированный IO-адрес).
  /// Блокирует до завершения. `config` — см. `CFG_DRAM_TO_SPI0_TX`.
  pub fn start(&self, config: u32, src: u32, dest: u32, n: u32) {
    unsafe {
      let d = &mut *addr_of_mut!(DESCRIPTOR);
      d.config = config;
      d.src = src;
      d.dest = dest;
      d.byte_count = n;
      d.param = 0;
      d.link = LINK_END;
    }

    // 1. Загружаем адрес дескриптора в канал.
    let desc_addr = addr_of_mut!(DESCRIPTOR) as u32;
    self.write_reg(DESC_ADDR_REG, desc_addr);
    // Подтверждаем запись (мануал, 3.9.4.3): ждём пока прочитается то же значение.
    while self.read_reg(DESC_ADDR_REG) != desc_addr {
      core::hint::spin_loop();
    }

    // 2. Включаем канал — DMAC читает дескриптор и стартует.
    self.write_reg(EN_REG, 1);
  }

  /// Дождаться завершения переноса (byte counter обнуляется, канал сам вырубается).
  pub fn wait_done(&self) {
    while self.read_reg(EN_REG) & 1 != 0 {
      core::hint::spin_loop();
    }
  }

  /// Сколько байт ещё осталось перенести (для отладки/прогресса).
  pub fn bytes_left(&self) -> u32 {
    self.read_reg(BCNT_LEFT_REG)
  }
}
