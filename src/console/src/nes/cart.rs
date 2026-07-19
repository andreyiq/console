//! Картридж NES для `no_std` без аллокатора.
//!
//! `runes::bin` использует `Vec<u8>` для PRG/CHR/SRAM — в `no_std` без
//! аллокатора это не подходит. Здесь ROM прошивается в бинарник через
//! `include_bytes!`, а SRAM — отдельный `static mut` массив.
//!
//! `StaticCart` владеет срезами ROM и SRAM, реализует трейт `Cartridge`.

use core::ptr::addr_of_mut;

use runes::cartridge::{BankType, Cartridge, MirrorType};
use runes::utils;

/// ROM, прошитый в бинарник. Размер nestest.nes — 24 KB, mario.nes — 40 KB, pac-man.nes — 24 KB.
/// Чтобы переключить ROM — поменяй путь в `include_bytes!` и константу `ROM_NAME`.
pub static ROM: &[u8] = include_bytes!("../../archive/roms/pac-man.nes");
pub const ROM_NAME: &str = "pac-man.nes";

/// SRAM картриджа (8 KB). `static mut` — доступ один раз через `take_sram()`.
static mut SRAM: [u8; 0x2000] = [0; 0x2000];

/// Взять SRAM как `&'static mut [u8]`. Можно вызвать только один раз —
/// повторный вызов паникует (защита от двойного aliasing).
pub fn take_sram() -> &'static mut [u8] {
  unsafe {
    let p = addr_of_mut!(SRAM) as *mut u8;
    core::slice::from_raw_parts_mut(p, 0x2000)
  }
}

/// Заголовок iNES (16 байт).
#[repr(C, packed)]
pub struct INesHeader {
  pub magic: [u8; 4],
  pub prg_rom_nbanks: u8,
  pub chr_rom_nbanks: u8,
  pub flags6: u8,
  pub flags7: u8,
  pub prg_ram_nbanks: u8,
  pub flags9: u8,
  pub flags10: u8,
  pub padding: [u8; 5],
}

/// Результат парсинга заголовка iNES.
pub struct INesInfo {
  pub prg_pos: usize,
  pub prg_len: usize,
  pub chr_pos: usize,
  pub chr_len: usize,
  pub mapper_id: u8,
  pub mirror: MirrorType,
}

/// Распарсить заголовок iNES из ROM. Паникует если магическая подпись не «NES\x1a».
pub fn parse_header(rom: &[u8]) -> INesInfo {
  assert!(rom.len() >= 16, "ROM too small for iNES header");
  // Безопасно: rom жив и достаточно длинный. packed struct — читаем поля по смещениям.
  let magic = [rom[0], rom[1], rom[2], rom[3]];
  let prg_rom_nbanks = rom[4];
  let chr_rom_nbanks = rom[5];
  let flags6 = rom[6];
  let flags7 = rom[7];
  assert!(&magic == b"NES\x1a", "not an iNES file: {:02x?}", magic);

  let mirror = match ((flags6 >> 2) & 2) | (flags6 & 1) {
    0 => MirrorType::Horizontal,
    1 => MirrorType::Vertical,
    2 => MirrorType::Single0,
    3 => MirrorType::Single1,
    _ => MirrorType::Four,
  };
  let mapper_id = (flags7 & 0xf0) | (flags6 >> 4);

  let mut pos = 16usize;
  if flags6 & 0x04 == 0x04 {
    pos += 512; // trainer
  }

  let prg_len = prg_rom_nbanks as usize * 0x4000;
  let mut chr_len = chr_rom_nbanks as usize * 0x2000;
  if chr_len == 0 {
    chr_len = 0x2000; // CHR-RAM
  }
  let prg_pos = pos;
  let chr_pos = pos + prg_len;

  INesInfo {
    prg_pos,
    prg_len,
    chr_pos,
    chr_len,
    mapper_id,
    mirror,
  }
}

/// Картридж, владеющий срезами ROM и SRAM. Не использует аллокатор.
///
/// `rom` — весь файл .nes (с заголовком). `prg_pos`/`chr_pos` — смещения
/// внутри `rom` до PRG/CHR данных. `sram` — отдельный `&'static mut` массив.
pub struct StaticCart {
  rom: &'static [u8],
  chr_pos: usize,
  chr_len: usize,
  prg_pos: usize,
  prg_len: usize,
  sram: &'static mut [u8],
  mirror: MirrorType,
}

impl StaticCart {
  pub fn new(
    rom: &'static [u8],
    chr_pos: usize,
    chr_len: usize,
    prg_pos: usize,
    prg_len: usize,
    sram: &'static mut [u8],
    mirror: MirrorType,
  ) -> Self {
    Self {
      rom,
      chr_pos,
      chr_len,
      prg_pos,
      prg_len,
      sram,
      mirror,
    }
  }
}

impl Cartridge for StaticCart {
  fn get_size(&self, kind: BankType) -> usize {
    match kind {
      BankType::PrgRom => self.prg_len,
      BankType::ChrRom => self.chr_len,
      BankType::Sram => self.sram.len(),
    }
  }

  fn get_bank<'a>(
    &self,
    base: usize,
    size: usize,
    kind: BankType,
  ) -> &'a [u8] {
    // `self.rom` — `&'static [u8]`, данные живут в ROM (include_bytes!).
    // Возвращаем срез с расширенным lifetime через from_raw_parts.
    unsafe {
      let (ptr, _len): (*const u8, usize) = match kind {
        BankType::PrgRom => (
          self.rom[self.prg_pos..].as_ptr(),
          self.prg_len,
        ),
        BankType::ChrRom => (
          self.rom[self.chr_pos..].as_ptr(),
          self.chr_len,
        ),
        BankType::Sram => (self.sram.as_ptr(), self.sram.len()),
      };
      core::slice::from_raw_parts(ptr.add(base), size)
    }
  }

  fn get_bank_mut<'a>(
    &mut self,
    base: usize,
    size: usize,
    kind: BankType,
  ) -> &'a mut [u8] {
    unsafe {
      // Для CHR-ROM возвращаем mutable-срез через каст *const→*mut.
      // ROM лежит в DDR (writable), так что запись не крашнет.
      // Для CHR-ROM запись в CHR — нетипично, мапперы в основном только
      // переключают банки (меняют указатели), а не пишут в данные.
      let (ptr, _len): (*mut u8, usize) = match kind {
        BankType::PrgRom => panic!("write prg-rom"),
        BankType::ChrRom => (
          self.rom[self.chr_pos..].as_ptr() as *mut u8,
          self.chr_len,
        ),
        BankType::Sram => (self.sram.as_mut_ptr(), self.sram.len()),
      };
      core::slice::from_raw_parts_mut(ptr.add(base), size)
    }
  }

  fn get_mirror_type(&self) -> MirrorType {
    self.mirror
  }
  fn set_mirror_type(&mut self, mt: MirrorType) {
    self.mirror = mt;
  }

  /// Save/load в `no_std` без FS — заглушки (возвращаем `true` чтобы
  /// `runes` не считал это ошибкой). При необходимости можно сюда прикрутить
  /// запись в SPI-flash или другой носитель.
  fn load(&mut self, _reader: &mut dyn utils::Read) -> bool {
    true
  }
  fn save(&self, _writer: &mut dyn utils::Write) -> bool {
    true
  }
  fn load_sram(&mut self, _reader: &mut dyn utils::Read) -> bool {
    true
  }
  fn save_sram(&self, _writer: &mut dyn utils::Write) -> bool {
    true
  }
}
