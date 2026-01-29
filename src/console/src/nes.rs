use core::mem::MaybeUninit;
use core::cell::RefCell;

use runes::apu;
use runes::cartridge::{BankType, Cartridge, MirrorType};
use runes::mapper;
use runes::ppu;
use runes::memory;
use runes::utils::{self, load_prefix, save_prefix, Read, Write};
use runes::controller::{stdctl, InputPoller};

// use crate::dma;
// use crate::gpio;
use crate::ili9844;

#[repr(C)]
pub struct Mapper2<'a, C>
where
  C: Cartridge,
{
  cart: C,
  prg_banks: [&'a [u8]; 2],
  chr_bank: &'a [u8],
  sram: &'a mut [u8],
  prg_nbank: usize,
}

impl<'a, C> memory::VMem for Mapper2<'a, C>
where
  C: Cartridge,
{
  fn read(&self, addr: u16) -> u8 {
    let addr = addr as usize;
    match addr >> 12 {
      /* [0x0000..0x2000) */
      0 | 1 => self.chr_bank[addr],
      /* [0x2000..0x6000) */
      2 | 3 | 4 | 5 => panic!("unmapped address: 0x{:04x}", addr),
      /* [0x6000..0x8000) */
      6 | 7 => self.sram[addr - 0x6000],
      /* [0x8000..0xffff] */
      _ => self.prg_banks[(addr >> 14) & 1][addr & 0x3fff],
    }
  }

  fn write(&mut self, addr: u16, data: u8) {
    let addr = addr as usize;
    match addr >> 12 {
      /* [0x0000..0x2000) */
      0 | 1 => {
        panic!("write chr_bank");
      },
      /* [0x2000..0x6000) */
      2 | 3 | 4 | 5 => panic!("unmapped address: 0x{:04x}", addr),
      /* [0x6000..0x8000) */
      6 | 7 => self.sram[addr - 0x6000] = data,
      /* [0x8000..0xffff] */
      _ => {
        self.prg_banks[0] = self.cart.get_bank(
          ((data as usize) % self.prg_nbank) << 14,
          0x4000,
          BankType::PrgRom,
        )
      }
    }
  }
}

impl<'a, C> Mapper2<'a, C>
where
    C: Cartridge,
{
  pub fn new(cart: C) -> Self {
    let nbank = cart.get_size(BankType::PrgRom) >> 14;
    unsafe {
      let mut m = Mapper2 {
        cart,
        prg_nbank: nbank,
        prg_banks: MaybeUninit::uninit().assume_init(),
        chr_bank: core::slice::from_raw_parts_mut(
          core::ptr::null_mut(),
          0,
        ),
        sram: core::slice::from_raw_parts_mut(core::ptr::null_mut(), 0),
      };
      let c = &mut m.cart;
      m.prg_banks = [
        c.get_bank(0, 0x4000, BankType::PrgRom),
        c.get_bank((nbank - 1) << 14, 0x4000, BankType::PrgRom),
      ];
      m.chr_bank = c.get_bank(0, 0x2000, BankType::ChrRom);
      m.sram = c.get_bank_mut(0, 0x2000, BankType::Sram);
      m
    }
  }
}

impl<'a, C> mapper::Mapper for Mapper2<'a, C>
where
    C: Cartridge,
{
  fn get_cart(&self) -> &dyn Cartridge {
    &self.cart
  }
  fn get_cart_mut(&mut self) -> &mut dyn Cartridge {
    &mut self.cart
  }

  fn load(&mut self, reader: &mut dyn Read) -> bool {
    for v in self.prg_banks.iter_mut() {
      let mut offset: usize = 0;
      if !load_prefix(&mut offset, 0, reader) {
        return false
      }
      *v = self.cart.get_bank(offset, 0x4000, BankType::PrgRom);
    }
    let mut offset: usize = 0;
    if !load_prefix(&mut offset, 0, reader) {
      return false
    }
    self.chr_bank =
      self.cart.get_bank(offset, 0x2000, BankType::ChrRom);
    self.cart.load(reader)
  }

  fn save(&self, writer: &mut dyn Write) -> bool {
    let prg_base = self.cart.get_bank(0, 0, BankType::PrgRom).as_ptr();
    let chr_base = self.cart.get_bank(0, 0, BankType::ChrRom).as_ptr();
    for v in self.prg_banks.iter() {
      if !save_prefix(
        &(v.as_ptr() as usize - prg_base as usize),
        0,
        writer,
      ) {
        return false
      }
    }
    if !save_prefix(
      &(self.chr_bank.as_ptr() as usize - chr_base as usize),
      0,
      writer,
    ) {
      return false
    }
    self.cart.save(writer)
  }
}

/* FIXME
const RGB_COLORS: [u32; 64] = [
  0x666666, 0x002a88, 0x1412a7, 0x3b00a4, 0x5c007e, 0x6e0040, 0x6c0600,
  0x561d00, 0x333500, 0x0b4800, 0x005200, 0x004f08, 0x00404d, 0x000000,
  0x000000, 0x000000, 0xadadad, 0x155fd9, 0x4240ff, 0x7527fe, 0xa01acc,
  0xb71e7b, 0xb53120, 0x994e00, 0x6b6d00, 0x388700, 0x0c9300, 0x008f32,
  0x007c8d, 0x000000, 0x000000, 0x000000, 0xfffeff, 0x64b0ff, 0x9290ff,
  0xc676ff, 0xf36aff, 0xfe6ecc, 0xfe8170, 0xea9e22, 0xbcbe00, 0x88d800,
  0x5ce430, 0x45e082, 0x48cdde, 0x4f4f4f, 0x000000, 0x000000, 0xfffeff,
  0xc0dfff, 0xd3d2ff, 0xe8c8ff, 0xfbc2ff, 0xfec4ea, 0xfeccc5, 0xf7d8a5,
  0xe4e594, 0xcfef96, 0xbdf4ab, 0xb3f3cc, 0xb5ebf2, 0xb8b8b8, 0x000000,
  0x000000,
];
*/

pub const PIX_WIDTH: u16 = 256;
pub const PIX_HEIGHT: u16 = 240;

pub struct SimpleCart<'a> {
  rom: &'a [u8],
  chr_pos: usize,
  chr_len: usize,
  prg_pos: usize,
  prg_len: usize,
  sram: &'a mut [u8],
  pub mirror_type: MirrorType,
}

impl<'a> SimpleCart<'a> {
  pub fn new(
    rom: &'a [u8],
    chr_pos: usize,
    chr_len: usize,
    prg_pos: usize,
    prg_len: usize,
    sram: &'a mut [u8],
    mirror_type: MirrorType,
  ) -> Self {
    SimpleCart {
      rom, chr_pos, chr_len, prg_pos, prg_len, sram, mirror_type,
    }
  }

  /* FIXME
  fn load_slice(slice: &mut [u8], reader: &mut dyn utils::Read) -> bool {
    let len = slice.len();
    match reader.read(slice) {
      Some(x) => x == len,
      None => false,
    }
  }
  */

  fn save_slice(slice: &[u8], pos: usize, len: usize, writer: &mut dyn utils::Write) -> bool {
    match writer.write(&slice[pos..(pos + len)]) {
      Some(x) => x == len,
      None => false,
    }
  }
}

impl<'a> Cartridge for SimpleCart<'a> {
  fn get_size(&self, kind: BankType) -> usize {
    match kind {
      BankType::PrgRom => self.prg_len,
      BankType::ChrRom => self.chr_len,
      BankType::Sram => self.sram.len(),
    }
  }
  fn get_bank<'b>(
    &self,
    base: usize,
    size: usize,
    kind: BankType,
  ) -> &'b [u8] {
    unsafe {
      &*((&(match kind {
        BankType::PrgRom => {
          &self.rom[self.prg_pos..(self.prg_pos + self.prg_len)]
        },
        BankType::ChrRom => {
          &self.rom[self.chr_pos..(self.chr_pos + self.chr_len)]
        },
        BankType::Sram => &self.sram,
      })[base..base + size]) as *const [u8])
    }
  }

  fn get_bank_mut<'b>(
    &mut self,
    base: usize,
    size: usize,
    kind: BankType,
  ) -> &'b mut [u8] {
    unsafe {
      &mut *((&mut (match kind {
        BankType::PrgRom => {
          panic!("write prg-rom");
        },
        BankType::ChrRom => {
          panic!("write chr-rom");
        },
        BankType::Sram => &mut self.sram,
      })[base..base + size]) as *mut [u8])
    }
  }

  fn get_mirror_type(&self) -> MirrorType {
    self.mirror_type
  }
  fn set_mirror_type(&mut self, mt: MirrorType) {
    self.mirror_type = mt
  }

  fn load(&mut self, reader: &mut dyn utils::Read) -> bool {
    self.load_sram(reader) &&
      //SimpleCart::load_slice(&mut self.chr_rom, reader) &&
      utils::load_prefix(&mut self.mirror_type, 0, reader)
  }

  fn save(&self, writer: &mut dyn utils::Write) -> bool {
    self.save_sram(writer) &&
      SimpleCart::save_slice(&self.rom, self.chr_pos, self.chr_len, writer) &&
      utils::save_prefix(&self.mirror_type, 0, writer)
  }
fn load_sram(&mut self, reader: &mut dyn utils::Read) -> bool {
    let len = self.sram.len();
    match reader.read(&mut self.sram) {
      Some(x) => x == len,
      None => false,
    }
  }

  fn save_sram(&self, writer: &mut dyn utils::Write) -> bool {
    let len = self.sram.len();
    match writer.write(&self.sram) {
      Some(x) => x == len,
      None => false,
    }
  }
}

/* FIXME
#[inline(always)]
fn get_rgb(color: u8) -> [u8; 3] {
  let c = RGB_COLORS[color as usize];
  [(c >> 16) as u8, ((c >> 8) & 0xff) as u8, (c & 0xff) as u8]
}
*/

pub struct Screen<'a> {
  pub frame_buf: &'a mut [u8],
}

impl<'a> Screen<'a> {
  pub fn new(frame_buf: &'a mut [u8]) -> Self {
    Self { frame_buf }
  }
}

// FIXME
/*
fn dma_send(_data: &[u8]) {
  unsafe {
    //while read_volatile(dma::DMA1_CNTR3) != 0 {}
    write_volatile(dma::DMA1_MADDR3, data.as_ptr() as u32);
    write_volatile(dma::DMA1_CNTR3, data.len() as u32);

    let mut val = read_volatile(dma::DMA1_CFGR3);
    val |= dma::DMA1_CFGR3_EN;
    write_volatile(dma::DMA1_CFGR3, val);

    while read_volatile(dma::DMA1_CNTR3) != 0 {}
  }
}
*/

impl<'a> ppu::Screen for Screen<'a> {
  #[inline(always)]
  fn put(&mut self, _x: u8, _y: u8, _color: u8) {
    // FIXME
    /*
    let rgb = get_rgb(color);
    if x == 0 && y != 0 {
      unsafe {
        // write data
        write_volatile(gpio::GPIOA_BSHR, 1 << 3);
      }
      dma_send(&self.frame_buf);
    }
    self.frame_buf[x as usize * 3] = rgb[0];
    self.frame_buf[x as usize * 3 + 1] = rgb[1];
    self.frame_buf[x as usize * 3 + 2] = rgb[2];
    // ili9844::write_data(&rgb);
    */
  }

  fn render(&mut self) {
    pub const GPIO_PE_DAT: *mut u32 = 0x0200_00D0 as *mut u32;
    unsafe { GPIO_PE_DAT.write_volatile(0xFFFFFFFF); }
    crate::utils::delay(1);
    unsafe { GPIO_PE_DAT.write_volatile(0x0); }
  }

  fn frame(&mut self) {
    ili9844::set_window((ili9844::WIDTH - PIX_WIDTH) / 2, (ili9844::HEIGHT - PIX_HEIGHT) / 2, PIX_WIDTH, PIX_HEIGHT);
    ili9844::write_command(ili9844::Command::MemoryWrite);
  }
}

pub struct Speaker {}

impl apu::Speaker for Speaker {
  fn queue(&mut self, _sample: i16) {
  }
}

pub struct Joystick {
  prev: RefCell<u8>,
}

impl Joystick {
  pub fn new() -> Self {
    Self { prev: RefCell::new(stdctl::NULL) }
  }
}

impl InputPoller for Joystick {
  #[inline]
  fn poll(&self) -> u8 {
    let mut prev = self.prev.borrow_mut();
    if *prev == stdctl::NULL {
      *prev = stdctl::SELECT;
    } else {
      *prev = stdctl::NULL;
    }
    *prev
  }
}

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

pub static ROM: &[u8] = include_bytes!("../roms/mario.nes");
pub const ROM_LEN: usize = include_bytes!("../roms/mario.nes").len();
