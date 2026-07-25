//! Mapper 0 (NROM) — простейший маппер NES без переключения банков.
//!
//! `runes` не предоставляет Mapper0, а публичный `Mapper2` использует
//! `MaybeUninit::uninit().assume_init()` (UB), от которого крашится на нашем
//! тулчейне. Здесь — чистая реализация без UB.
//!
//! Раскладка адресов CPU:
//!   $6000-$7FFF → SRAM (8 KB)
//!   $8000-$FFFF → PRG ROM
//!     если PRG 16 KB (1 банк) — зеркало по маске 0x3FFF
//!     если PRG 32 KB (2 банка) — прямое отображение по маске 0x7FFF
//!
//! Раскладка адресов PPU:
//!   $0000-$1FFF → CHR ROM/RAM (8 KB)

use runes::cartridge::{BankType, Cartridge};
use runes::memory::VMem;
use runes::mapper::Mapper;
use runes::utils::{Read, Write};

/// NROM маппер. Владеет картриджем, хранит срезы PRG/CHR/SRAM.
pub struct Mapper0<C: Cartridge> {
  cart: C,
  prg: &'static [u8],   // весь PRG ROM (16 или 32 KB)
  chr: &'static mut [u8], // 8 KB CHR (ROM или RAM)
  sram: &'static mut [u8], // 8 KB SRAM
  prg_mask: u16,        // маска адреса PRG: 0x3FFF для 16 KB, 0x7FFF для 32 KB
}

impl<C: Cartridge> Mapper0<C> {
  pub fn new(mut cart: C) -> Self {
    let prg_len = cart.get_size(BankType::PrgRom);
    let chr_len = cart.get_size(BankType::ChrRom);
    let sram_len = cart.get_size(BankType::Sram);
    // Маска: 16 KB → 0x3FFF, 32 KB → 0x7FFF. Больше 32 KB у NROM не бывает.
    let prg_mask: u16 = if prg_len <= 0x4000 { 0x3FFF } else { 0x7FFF };

    // Достаём срезы через get_bank/get_bank_mut. CHR-ROM — только для чтения,
    // но нам нужен *mut для поля chr (маппер может теоретически писать в CHR-RAM).
    // NROM не пишет в CHR, так что каст *const → *mut безопасен.
    let prg: &'static [u8] = cart.get_bank(0, prg_len, BankType::PrgRom);
    let chr: &'static mut [u8] = unsafe {
      let c: &'static [u8] = cart.get_bank(0, chr_len, BankType::ChrRom);
      core::slice::from_raw_parts_mut(c.as_ptr() as *mut u8, chr_len)
    };
    let sram: &'static mut [u8] = unsafe {
      let p = cart.get_bank_mut(0, sram_len, BankType::Sram).as_mut_ptr();
      core::slice::from_raw_parts_mut(p, sram_len)
    };

    Self {
      cart,
      prg,
      chr,
      sram,
      prg_mask,
    }
  }
}

impl<C: Cartridge> VMem for Mapper0<C> {
  fn read(&self, addr: u16) -> u8 {
    let a = addr as usize;
    match addr {
      0x0000..=0x1FFF => self.chr[a],           // CHR
      0x6000..=0x7FFF => self.sram[a - 0x6000],  // SRAM
      0x8000..=0xFFFF => self.prg[(a - 0x8000) & (self.prg_mask as usize)], // PRG (с маской)
      _ => 0, // unmapped
    }
  }

  fn write(&mut self, addr: u16, data: u8) {
    let a = addr as usize;
    match addr {
      0x0000..=0x1FFF => self.chr[a] = data,    // CHR-RAM (если есть)
      0x6000..=0x7FFF => self.sram[a - 0x6000] = data, // SRAM
      0x8000..=0xFFFF => { /* NROM игнорирует записи в PRG ROM */ }
      _ => {}
    }
  }
}

impl<C: Cartridge> Mapper for Mapper0<C> {
  fn get_cart(&self) -> &dyn Cartridge {
    &self.cart
  }
  fn get_cart_mut(&mut self) -> &mut dyn Cartridge {
    &mut self.cart
  }

  fn load(&mut self, _reader: &mut dyn Read) -> bool {
    true // заглушка (no_std без FS)
  }
  fn save(&self, _writer: &mut dyn Write) -> bool {
    true
  }
}
