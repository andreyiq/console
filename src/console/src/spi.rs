//! SPI драйвер для F133. Раздел 9.3.6 user manual.
//!
//! Высокоуровневый API: `Spi::Spi0.init()`, `Spi::Spi0.send_byte(b)`.
//! Управление CS — ручное через GPIO (см. `cs_low`/`cs_high`): это даёт
//! полный контроль — CS можно держать низким между байтами, что удобно
//! для ILI9488 (одно «сообщение» = команда + данные под одним CS).
//!
//! Добавить новый SPI = добавить вариант в enum + match arm в `info()`.

use core::ptr::{read_volatile, write_volatile};

use crate::gpio::{self, Func, Pin};

// Смещения регистров SPI (одинаковые для SPI0 и SPI1).
const GCR: u32 = 0x04; // Global Control
const TCR: u32 = 0x08; // Transfer Control
const FCR: u32 = 0x18; // FIFO Control
const CLK_CTL: u32 = 0x24; // Clock divider
const MBC: u32 = 0x30; // Master Burst Counter
const MTC: u32 = 0x34; // Master Transmit Counter
const BCC: u32 = 0x38; // Master Burst Control
const TXD: u32 = 0x200; // TX Data
const RXD: u32 = 0x300; // RX Data
const NDMA_MODE_CTL: u32 = 0x88; // SPI Normal DMA Mode Control (9.3.6.16)

// Биты FCR
const RX_FIFO_RST: u32 = 1 << 6; // сброс RX FIFO
const TX_FIFO_RST: u32 = 1 << 30; // сброс TX FIFO

// Биты TCR
const XCH: u32 = 1 << 31; // Exchange Burst — старт передачи (auto-clear по окончании)
const DHB: u32 = 1 << 8; // Discard Hash Burst — только TX, без приёма
const SS_OWNER: u32 = 1 << 6; // 1 = SS управляется программно (GPIO), контроллер не трогает SS

// GCR: TP_EN | MODE (master) | EN
const GCR_EN: u32 = 0x83;

// NDMA_MODE_CTL (0x88): bits 7:6 = SPI_ACT_M.
// 10 = dma_active управляется DRQ (контроллер сам поднимает запрос к DMA когда FIFO пуст).
// bit 5 = ACK_M (1 = ждать ack), bits 4:0 = WAIT (5 по умолчанию). Дефолт 0xE5 → 0xA5.
const NDMA_DRQ_CONTROLLED: u32 = 0xA5;

/// SPI-контроллер F133.
pub enum Spi {
  Spi0,
  Spi1,
}

/// Конфигурация SPI: base адрес, пины (CLK/MOSI/MISO/CS) и функция (Spi0/Spi1).
struct SpiInfo {
  base: u32,
  clk: Pin,
  mosi: Pin,
  miso: Pin,
  cs: Pin,
  func: Func,
}

impl Spi {
  /// Описание SPI (адреса и пины). const fn — вычисляется в compile-time.
  const fn info(&self) -> SpiInfo {
    match self {
      Spi::Spi0 => SpiInfo {
        base: 0x0402_5000,
        clk: gpio::PC2,
        mosi: gpio::PC4,
        miso: gpio::PC5,
        cs: gpio::PC3,
        func: Func::Spi0,
      },
      Spi::Spi1 => SpiInfo {
        base: 0x0402_6000,
        clk: gpio::PD11,
        mosi: gpio::PD12,
        miso: gpio::PD13,
        cs: gpio::PD10,
        func: Func::Spi1,
      },
    }
  }

  #[inline]
  fn write_reg(&self, offset: u32, val: u32) {
    unsafe {
      write_volatile((self.info().base + offset) as *mut u32, val);
    }
  }

  #[inline]
  fn read_reg(&self, offset: u32) -> u32 {
    unsafe { read_volatile((self.info().base + offset) as *mut u32) }
  }

  /// Инициализация SPI: пины, делитель, FIFO, режим master TX-only.
  /// Перед вызовом нужно включить тактирование через `ccu::Peripheral::Spi0.enable()`.
  pub fn init(&self) {
    let info = self.info();
    info.clk.set_func(info.func);
    info.mosi.set_func(info.func);
    info.miso.set_func(info.func);
    info.cs.set_func(Func::Output);
    info.cs.set_high();

    self.write_reg(CLK_CTL, 0x1000); // делитель — медленно, удобно для анализатора
    self.write_reg(FCR, 0x8000_8000); // сброс TX и RX FIFO
    self.write_reg(FCR, 0x10001); // порог срабатывания = 1 байт
    self.write_reg(TCR, DHB | SS_OWNER); // только TX, без приёма; SS — программно
    self.write_reg(GCR, GCR_EN); // master mode + enable
  }

  /// Опустить CS (активный, низкий уровень).
  pub fn cs_low(&self) {
    self.info().cs.set_low();
  }

  /// Поднять CS (неактивный, высокий уровень).
  pub fn cs_high(&self) {
    self.info().cs.set_high();
  }

  /// Отправить один байт. CS управляется отдельно (`cs_low`/`cs_high`).
  pub fn send_byte(&self, byte: u8) {
    self.write_reg(BCC, 1);
    self.write_reg(MBC, 1);
    self.write_reg(MTC, 1);
    self.write_txd_byte(byte);
    self.write_reg(TCR, XCH | DHB | SS_OWNER); // старт
    while self.read_reg(TCR) & XCH != 0 {
      core::hint::spin_loop();
    }
  }

  /// Отправить буфер. Длина до 0xFFFFFF байт (ограничение MBC/MTC).
  pub fn send(&self, buf: &[u8]) {
    let n = buf.len() as u32;
    if n == 0 {
      return;
    }
    self.write_reg(BCC, n);
    self.write_reg(MBC, n);
    self.write_reg(MTC, n);
    for &b in buf {
      self.write_txd_byte(b);
    }
    self.write_reg(TCR, XCH | DHB | SS_OWNER);
    while self.read_reg(TCR) & XCH != 0 {
      core::hint::spin_loop();
    }
  }

  /// Побайтовая (8-бит) запись в TXD. По мануалу F133 (9.3.6.17) размер записи
  /// определяет сколько байт уйдёт в FIFO: byte → 1, half-word → 2, word → 4.
  /// 32-битная запись молча клала бы 4 байта (0x000000A9 → 00 00 00 A9).
  #[inline]
  fn write_txd_byte(&self, byte: u8) {
    unsafe {
      write_volatile((self.info().base + TXD) as *mut u8, byte);
    }
  }

  /// Прочитать один байт из RX FIFO (8-битная запись в RXD).
  #[inline]
  fn read_rxd_byte(&self) -> u8 {
    unsafe { read_volatile((self.info().base + RXD) as *const u8) }
  }

  /// Отправить `buf` и одновременно прочитать столько же байт с MISO.
  /// Используется для чтения регистров дисплея (например ID 0x04).
  /// DHB=0 → RX включён. Перед вызовом CS уже низко, DC выставлен вызывающим.
  pub fn send_recv(&self, buf: &[u8]) {
    let n = buf.len() as u32;
    if n == 0 {
      return;
    }
    // Сброс RX FIFO чтобы выкинуть мусор от прошлых транзакций.
    self.write_reg(FCR, TX_FIFO_RST | RX_FIFO_RST);
    self.write_reg(FCR, 0x10001);
    self.write_reg(BCC, n);
    self.write_reg(MBC, n);
    self.write_reg(MTC, n);
    for &b in buf {
      self.write_txd_byte(b);
    }
    self.write_reg(TCR, XCH); // DHB=0 → и TX, и RX; SS_OWNER=0 → контроллер не важен (CS уже низко)
    while self.read_reg(TCR) & XCH != 0 {
      core::hint::spin_loop();
    }
  }

  /// Прочитать `out` байт из RX FIFO (после send_recv).
  pub fn read_rx(&self, out: &mut [u8]) {
    for b in out.iter_mut() {
      *b = self.read_rxd_byte();
    }
  }

  /// Физический адрес регистра TXD (0x04025200 для SPI0).
  /// Нужен DMA как destination (DEST_ADDR_MODE=IO, адрес фиксирован).
  pub fn txd_addr(&self) -> u32 {
    self.info().base + TXD
  }

  /// Подготовить SPI к приёму данных от DMA: выставить счётчики burst на `n`
  /// байт, включить DMA-режим (DRQ-controlled) и стартовать обмен (XCH).
  /// Сам XCH не заполнит FIFO — он ждёт, пока DMA накачает данные через TXD.
  /// Вызывается ПОСЛЕ `dma.start(...)`, чтобы DMA уже кормил FIFO.
  pub fn prepare_dma(&self, n: u32) {
    self.write_reg(BCC, n);
    self.write_reg(MBC, n);
    self.write_reg(MTC, n);
    self.write_reg(NDMA_MODE_CTL, NDMA_DRQ_CONTROLLED);
    self.write_reg(TCR, XCH | DHB | SS_OWNER); // старт; FIFO пока пуст — SPI ждёт данные
  }

  /// Готов ли burst (XCH сбросился = все байты отправлены).
  pub fn burst_done(&self) -> bool {
    self.read_reg(TCR) & XCH == 0
  }

  /// Дождаться завершения burst (когда DMA выкачал все данные и SPI отправил).
  pub fn wait_burst_done(&self) {
    while !self.burst_done() {
      core::hint::spin_loop();
    }
  }
}
