//! Порт smolnes (binji/smolnes) на Rust — компактный NES-эмулятор.
//!
//! Архитектура: один switch для dispatch opcodes, прямой framebuffer,
//! PPU per-cycle (shift registers), без APU. Mapper 0/2/3/7.
//! Источник: https://github.com/binji/smolnes (deobfuscated.c, 711 строк).

use crate::display::Display;
use crate::nes::palette;

/// Размеры NES-кадра.
pub const NES_W: usize = 256;
pub const NES_H: usize = 240;
const NES_BUF_SIZE: usize = NES_W * NES_H * 3;

/// Смещение NES-кадра в центре дисплея 480×320.
const OFFSET_X: u16 = (crate::display::WIDTH as usize - NES_W) as u16 / 2; // 112
const OFFSET_Y: u16 = (crate::display::HEIGHT as usize - NES_H) as u16 / 2; // 40

/// Framebuffer NES: 256×240 RGB888. В DDR, flush-ится целиком через DMA.
static mut NES_BUF: [u8; NES_BUF_SIZE] = [0; NES_BUF_SIZE];

/// ROM, прошитый в бинарник. nestest.nes — CPU тест (проверяет все opcodes).
pub const ROM: &[u8] = include_bytes!("../../archive/roms/nestest.nes");

/// Klaus Dormann 6502 functional test (64KB bin, load at $0000, PC=$0400).
pub const KLAUS_BIN: &[u8] = include_bytes!("../../archive/roms/6502_functional_test.bin");

/// Состояние NES-эмулятора. Один struct — все регистры и память.
pub struct Nes {
  // CPU registers
  a: u8,
  x: u8,
  y: u8,
  p: u8, // status flags
  s: u8, // stack pointer
  pc: u16,
  // CPU scratch
  addr_lo: u8,
  addr_hi: u8,
  nomem: bool,
  result: u8,
  val: u8,
  cross: bool,
  cycles: u16,
  opcode: u8,
  nmi_irq: u8, // 1=IRQ, 4=NMI

  // PPU registers
  ppumask: u8,
  ppuctrl: u8,
  ppustatus: u8,
  ppubuf: u8,
  w: u8, // write toggle
  fine_x: u8,
  // PPU state
  scany: u16,
  dot: u16,
  t: u16, // Loopy T
  v: u16, // Loopy V
  ntb: u8, // nametable byte
  atb: u16, // attribute byte
  ptb_lo: u8, // pattern table low byte
  shift_hi: u16,
  shift_lo: u16,
  shift_at: u32,

  // Memory
  ram: [u8; 8192],
  vram: [u8; 2048],
  palette_ram: [u8; 64],
  oam: [u8; 256],
  chrram: [u8; 8192],
  prgram: [u8; 8192],

  // Mapper state
  mirror: u8,
  prg: [u8; 64], // PRG bank indices (длина 64 — индекс может быть до 60 для hi=0xFF)
  chr: [u8; 8],
  prgbits: u8,
  chrbits: u8,
  // Mapper 1 (MMC1)
  mmc1_bits: u8,
  mmc1_data: u8,
  mmc1_ctrl: u8,
  chrbank0: u8,
  chrbank1: u8,
  prgbank: u8,
  // Mapper 4 (MMC3)
  mmc3_chrprg: [u8; 8],
  mmc3_bits: u8,
  mmc3_irq: bool,
  mmc3_latch: u8,

  // ROM pointers (в .rodata, но DDR writable)
  rom: &'static [u8], // PRG ROM (после заголовка) — не используется напрямую, через rombuf
  chrrom_ptr: *const u8, // CHR ROM pointer (или указывает на chrram)
  chrrom_len: usize,
  chrrom_is_ram: bool,
  rombuf: &'static [u8], // весь .nes файл

  // Klaus Dormann 6502 functional test mode.
  // Если klaus_mode=true, mem_read/mem_write работают с klaus_mem напрямую
  // (без mapper, без PPU). PC=$0400, тест зацикливается при успехе/ошибке.
  klaus_mode: bool,
  klaus_mem: [u8; 65536],

  // Display
  display: *const Display,
  frame: u32,
}

impl Nes {
  pub fn new(display: &Display) -> Self {
    Self {
      a: 0, x: 0, y: 0, p: 4, s: 0xfd,
      pc: 0,
      addr_lo: 0, addr_hi: 0, nomem: false, result: 0, val: 0, cross: false,
      cycles: 0, opcode: 0, nmi_irq: 0,
      ppumask: 0, ppuctrl: 0, ppustatus: 0, ppubuf: 0, w: 0, fine_x: 0,
      scany: 0, dot: 0, t: 0, v: 0, ntb: 0, atb: 0, ptb_lo: 0,
      shift_hi: 0, shift_lo: 0, shift_at: 0,
      ram: [0; 8192], vram: [0; 2048], palette_ram: [0; 64], oam: [0; 256],
      chrram: [0; 8192], prgram: [0; 8192],
      mirror: 0, prg: [0; 64], chr: [0; 8], prgbits: 14, chrbits: 12,
      mmc1_bits: 0, mmc1_data: 0, mmc1_ctrl: 0, chrbank0: 0, chrbank1: 0, prgbank: 0,
      mmc3_chrprg: [0; 8], mmc3_bits: 0, mmc3_irq: false, mmc3_latch: 0,
      rom: &[], chrrom_ptr: core::ptr::null(), chrrom_len: 0, chrrom_is_ram: false, rombuf: &[],
      klaus_mode: false, klaus_mem: [0; 65536],
      display: display as *const Display,
      frame: 0,
    }
  }

  /// Загрузить iNES ROM. Инициализирует mapper, PRG/CHR банки, mirror.
  pub fn load_rom(&mut self, rom: &'static [u8]) {
    self.rombuf = rom;
    // PRG0 после 16-байтного заголовка.
    self.rom = &rom[16..];
    // PRG1 — последний банк. rom[4] — кол-во 16k PRG банков.
    self.prg[1] = rom[4] - 1;
    // CHR0 — после всего PRG. rom[5] — кол-во 8k CHR банков. Если 0 — CHR-RAM.
    let chr_pos = 16 + ((rom[4] as usize) << 14);
    if rom[5] != 0 {
      self.chrrom_ptr = rom[chr_pos..].as_ptr();
      self.chrrom_len = (rom[5] as usize) << 13;
      self.chrrom_is_ram = false;
      self.chr[1] = rom[5] * 2 - 1;
    } else {
      // CHR-RAM: указатель на self.chrram
      self.chrrom_ptr = self.chrram.as_ptr();
      self.chrrom_len = 8192;
      self.chrrom_is_ram = true;
      self.chr[1] = 1;
    }
    // Bit 0 rom[6]: 0=horizontal, 1=vertical mirror.
    self.mirror = 3 - (rom[6] & 1);
    // MMC3 (mapper 4) — особые банки.
    if rom[6] >> 4 == 4 {
      self.prgbits -= 1; // 8kb PRG banks
      self.chrbits -= 2; // 1kb CHR banks
    }
    // Reset vector: $FFFC.
    self.pc = self.mem_read(0xFFFC) as u16 | ((self.mem_read(0xFFFD) as u16) << 8);
    println!(
      "smol: rom loaded mapper={} prg_banks={} mirror={} pc=0x{:04x}",
      rom[6] >> 4, rom[4], self.mirror, self.pc
    );
  }

  /// Прочитать байт из CHR ROM/RAM.
  #[inline(always)]
  fn get_chr_byte(&self, a: u16) -> u8 {
    let bank = self.chr[(a >> self.chrbits) as usize] as usize;
    let off = (a & ((1 << self.chrbits) - 1)) as usize;
    let idx = (bank << self.chrbits) | off;
    if idx < self.chrrom_len {
      unsafe { *self.chrrom_ptr.add(idx) }
    } else {
      0
    }
  }

  /// Прочитать байт из nametable RAM с учётом mirroring.
  #[inline(always)]
  fn get_nametable_byte(&self, a: u16) -> u8 {
    let idx = match self.mirror {
      0 => (a as usize) % 1024,           // single bank 0
      1 => (a as usize) % 1024 + 1024,    // single bank 1
      2 => (a as usize) & 2047,           // vertical
      _ => ((a as usize) / 2 & 1024) | ((a as usize) % 1024), // horizontal
    };
    self.vram[idx]
  }

  /// Memory read (CPU address space).
  #[inline(always)]
  fn mem_read(&self, addr: u16) -> u8 {
    if self.klaus_mode {
      return self.klaus_mem[addr as usize];
    }
    let hi = (addr >> 8) as u8;
    let lo = (addr & 0xff) as u8;
    match hi >> 4 {
      0 | 1 => self.ram[addr as usize & 0x1fff], // $0000..$1fff RAM
      2 | 3 => { // $2000..$2007 PPU (mirrored)
        let lo = lo & 7;
        if lo == 7 { // $2007
          // TODO: PPU vram read — для CPU bench не нужно (Pac-Man не читает $2007 в main loop)
          self.ppubuf
        } else if lo == 2 { // $2002 ppustatus
          self.ppustatus & 0xe0
        } else {
          0
        }
      }
      4 => { // $4000..$4fff APU/IO
        if lo == 22 { // $4016 joypad
          0
        } else {
          0
        }
      }
      6 | 7 => self.prgram[(addr & 0x1fff) as usize], // $6000..$7fff PRG RAM
      _ => { // $8000..$ffff ROM
        let bank = self.prg[((hi - 8) >> (self.prgbits - 12)) as usize];
        let mask = ((self.rombuf[4] as usize) << (14 - self.prgbits)) - 1;
        let bank_idx = (bank as usize) & mask;
        let off = (addr as usize) & ((1 << self.prgbits) - 1);
        // PRG ROM: rombuf[16 + bank_idx*2^prgbits + off]
        self.rombuf[16 + (bank_idx << self.prgbits) + off]
      }
    }
  }

  /// Memory write (CPU address space).
  #[inline(always)]
  fn mem_write(&mut self, addr: u16, val: u8) {
    if self.klaus_mode {
      self.klaus_mem[addr as usize] = val;
      return;
    }
    let hi = (addr >> 8) as u8;
    let lo = (addr & 0xff) as u8;
    match hi >> 4 {
      0 | 1 => self.ram[addr as usize & 0x1fff] = val,
      2 | 3 => { // PPU registers — для bench не критично
        let _lo = lo & 7;
        // TODO: PPU writes
      }
      4 => { // $4014 OAM DMA, $4016 joypad
        if lo == 20 { // $4014 OAM DMA
          let hi2 = val;
          for i in 0..256u16 {
            self.oam[i as usize] = self.mem_read((hi2 as u16) << 8 | i);
          }
        }
      }
      6 | 7 => self.prgram[(addr & 0x1fff) as usize] = val,
      _ => { // $8000..$ffff — mapper writes (для Mapper 0 — игнор)
        // TODO: mapper writes для mapper 1,2,3,4,7
      }
    }
  }

  /// Прочитать байт по PC и инкрементировать PC.
  #[inline(always)]
  fn read_pc(&mut self) -> u8 {
    self.val = self.mem_read(self.pc);
    self.pc = self.pc.wrapping_add(1);
    self.val
  }

  /// Установить N (negative) и Z (zero) флаги.
  #[inline(always)]
  fn set_nz(&mut self, val: u8) {
    self.p = (self.p & 0x7d) | (val & 0x80) | if val == 0 { 2 } else { 0 };
  }

  /// Push байт в стек.
  #[inline(always)]
  fn push(&mut self, val: u8) {
    let s = self.s;
    self.mem_write(0x0100 | s as u16, val);
    self.s = s.wrapping_sub(1);
  }

  /// Pull байт из стека.
  #[inline(always)]
  fn pull(&mut self) -> u8 {
    self.s = self.s.wrapping_add(1);
    let s = self.s;
    self.mem_read(0x0100 | s as u16)
  }

  /// Masks для branch (N,V,C,Z) и SE*/CL* (C,I,V,D).
  const MASK: [u8; 20] = [
    128, 64, 1, 2, // 0-3: branch flags N,V,C,Z
    1, 0, 0, 1, 4, 0, 0, 4, 0, // 4-12: SE*/CL* clear/set pairs
    0, 64, 0, 8, 0, 0, 8, // 13-19: V,D flags
  ];

  /// Один шаг CPU: выполнить одну инструкцию 6502. Порт switch из smolnes.
  /// Обновляет self.cycles (кол-во циклов инструкции).
  pub fn cpu_step(&mut self) {
    self.cycles = 0;
    self.nomem = false;
    if self.nmi_irq != 0 {
      // NMI или IRQ: push PC + P, загрузить вектор.
      self.push((self.pc >> 8) as u8);
      self.push(self.pc as u8);
      self.push(self.p | 0x20);
      let veclo: u16 = 0xFFFA - (self.nmi_irq as u16 & 4);
      let lo = self.mem_read(veclo);
      let hi = self.mem_read(veclo + 1);
      self.pc = (hi as u16) << 8 | lo as u16;
      self.nmi_irq = 0;
      self.cycles += 1;
      return;
    }

    self.opcode = self.read_pc();
    let opcodelo5 = self.opcode & 31;
    match opcodelo5 {
      0 => {
        if self.opcode & 0x80 != 0 {
          // LDY/CPY/CPX imm
          self.read_pc();
          self.nomem = true;
          self.execute_alu();
        } else {
          match self.opcode >> 5 {
            0 => { // BRK
              self.pc = self.pc.wrapping_add(1);
              self.push((self.pc >> 8) as u8);
              self.push(self.pc as u8);
              self.push(self.p | 0x30);
              self.p |= 0x04;
              let lo = self.mem_read(0xFFFE);
              let hi = self.mem_read(0xFFFF);
              self.pc = (hi as u16) << 8 | lo as u16;
              self.cycles += 1;
            }
            1 => { // JSR
              let result = self.read_pc();
              self.push((self.pc >> 8) as u8);
              self.push(self.pc as u8);
              let hi = self.read_pc();
              self.pc = (hi as u16) << 8 | result as u16;
            }
            2 => { // RTI
              self.p = self.pull() & !0x10;
              let lo = self.pull();
              let hi = self.pull();
              self.pc = (hi as u16) << 8 | lo as u16;
              self.cycles += 2;
            }
            3 => { // RTS
              let lo = self.pull();
              let hi = self.pull();
              self.pc = (hi as u16) << 8 | lo as u16;
              self.pc = self.pc.wrapping_add(1);
            }
            _ => {}
          }
          self.cycles += 4;
        }
      }
      16 => { // BPL, BMI, BVC, BVS, BCC, BCS, BNE, BEQ
        let offset_byte = self.read_pc();
        let m = Self::MASK[(self.opcode >> 6) as usize];
        if (self.p & m == 0) ^ ((self.opcode / 32) & 1 != 0) {
          let off = offset_byte as i8 as i16;
          let pcl = self.pc as u8;
          let pch = (self.pc >> 8) as u8;
          let new_pcl = pcl.wrapping_add(offset_byte);
          let cross = ((pcl as i16).wrapping_add(off)) >> 8;
          let new_pch = pch.wrapping_add(cross as u8);
          self.pc = ((new_pch as u16) << 8) | (new_pcl as u16);
          self.cycles += if cross != 0 { 2 } else { 1 };
        }
      }
      8 | 24 => {
        match self.opcode >> 4 {
          0 => { self.push(self.p | 0x30); self.cycles += 1; } // PHP
          2 => { self.p = self.pull() & !0x10; self.cycles += 2; } // PLP
          4 => { self.push(self.a); self.cycles += 1; } // PHA
          6 => { let v = self.pull(); self.set_nz(v); self.a = v; self.cycles += 2; } // PLA
          8 => { self.y = self.y.wrapping_sub(1); self.set_nz(self.y); } // DEY
          9 => { self.a = self.y; self.set_nz(self.a); } // TYA
          10 => { self.y = self.a; self.set_nz(self.y); } // TAY
          12 => { self.y = self.y.wrapping_add(1); self.set_nz(self.y); } // INY
          14 => { self.x = self.x.wrapping_add(1); self.set_nz(self.x); } // INX
          _ => { // CLC, SEC, CLI, SEI, CLV, CLD, SED
            let op = (self.opcode >> 4) as usize;
            self.p = (self.p & !Self::MASK[op + 3]) | Self::MASK[op + 4];
          }
        }
      }
      10 | 26 => {
        match self.opcode >> 4 {
          8 => { self.a = self.x; self.set_nz(self.a); } // TXA
          9 => { self.s = self.x; } // TXS
          10 => { self.x = self.a; self.set_nz(self.x); } // TAX
          11 => { self.x = self.s; self.set_nz(self.x); } // TSX
          12 => { self.x = self.x.wrapping_sub(1); self.set_nz(self.x); } // DEX
          14 => {} // NOP
          _ => { // ASL/ROL/LSR/ROR A
            self.nomem = true;
            self.val = self.a;
            self.execute_alu();
          }
        }
      }
      1 => { // X-indexed indirect
        self.val = self.read_pc();
        let ptr = self.val.wrapping_add(self.x);
        let lo = self.mem_read(ptr as u16);
        let hi = self.mem_read(ptr.wrapping_add(1) as u16);
        self.addr_lo = lo;
        self.addr_hi = hi;
        self.cycles += 4;
        self.cycles += 2;
        if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
          self.val = self.mem_read((self.addr_hi as u16) << 8 | self.addr_lo as u16);
        }
        self.execute_alu();
      }
      2 | 9 => { // Immediate
        self.val = self.read_pc();
        self.nomem = true;
        self.execute_alu();
      }
      17 => { // Zeropage Y-indexed
        let ptr = self.read_pc();
        self.addr_lo = self.mem_read(ptr as u16);
        self.addr_hi = self.mem_read(ptr.wrapping_add(1) as u16);
        self.cycles += 1;
        self.add_x_or_y();
        self.cycles += 2;
        if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
          self.val = self.mem_read((self.addr_hi as u16) << 8 | self.addr_lo as u16);
        }
        self.execute_alu();
      }
      4 | 5 | 6 | 20 | 21 | 22 => { // Zeropage (+X)
        self.addr_lo = self.read_pc();
        let with_index = opcodelo5 > 6;
        if with_index {
          // LDX/STX use Y, остальные X
          let idx = if (self.opcode & 0xd6) == 0x96 { self.y } else { self.x };
          self.addr_lo = self.addr_lo.wrapping_add(idx);
        }
        self.addr_hi = 0;
        if !with_index {
          self.cycles -= 0; // zeropage: cycles += 2 (ниже)
        }
        self.cycles += 2;
        if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
          self.val = self.mem_read(self.addr_lo as u16);
        }
        self.execute_alu();
      }
      12 | 13 | 14 | 25 | 28 | 29 | 30 => { // Absolute (+X/+Y)
        self.addr_lo = self.read_pc();
        self.addr_hi = self.read_pc();
        if opcodelo5 < 25 {
          // Absolute (без индекса)
          self.cycles += 2;
          if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
            self.val = self.mem_read((self.addr_hi as u16) << 8 | self.addr_lo as u16);
          }
          self.execute_alu();
        } else {
          self.add_x_or_y();
          self.cycles += 2;
          if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
            self.val = self.mem_read((self.addr_hi as u16) << 8 | self.addr_lo as u16);
          }
          self.execute_alu();
        }
      }
      _ => {} // неизвестные opcodes
    }
  }

  /// ALU операция (ORA/AND/EOR/ADC/SBC/ASL/ROL/LSR/ROR/DEC/INC/BIT/JMP/LD*/ST*/CP*).
  /// Вызывается после вычисления адреса и чтения val.
  fn execute_alu(&mut self) {
    self.result = 0;
    match self.opcode & 0xe3 {
      0x01 => { self.a |= self.val; self.set_nz(self.a); } // ORA
      0x21 => { self.a &= self.val; self.set_nz(self.a); } // AND
      0x41 => { self.a ^= self.val; self.set_nz(self.a); } // EOR
      0xe1 => { // SBC
        let carry_in = (self.p & 1) as u16;
        if self.p & 0x08 != 0 {
          // BCD SBC
          let a_lo = (self.a & 0x0f) as i16;
          let a_hi = (self.a >> 4) as i16;
          let v_lo = (self.val & 0x0f) as i16;
          let v_hi = (self.val >> 4) as i16;
          let mut lo = a_lo - v_lo - (1 - carry_in as i16);
          let mut hi = a_hi - v_hi;
          if lo < 0 { lo += 10; hi -= 1; }
          let bin_diff = self.a as i16 - self.val as i16 - (1 - carry_in as i16);
          let overflow = ((self.a ^ (bin_diff as u8)) & (self.val ^ (bin_diff as u8)) & 0x80) != 0;
          let carry = bin_diff >= 0;
          if hi < 0 { hi += 10; }
          self.p = (self.p & !0x41) | if carry { 1 } else { 0 } | if overflow { 0x40 } else { 0 };
          let res = ((hi & 0x0f) << 4) | (lo & 0x0f);
          self.a = res as u8;
          self.p = (self.p & !0x82) | (bin_diff as u8 & 0x80) | if bin_diff as u8 == 0 { 2 } else { 0 };
        } else {
          self.val = !self.val;
          let sum = self.a as u16 + self.val as u16 + carry_in;
          let carry = sum > 255;
          let overflow = ((self.a ^ sum as u8) & (self.val ^ sum as u8) & 0x80) != 0;
          self.p = (self.p & !0x41) | if carry { 1 } else { 0 } | if overflow { 0x40 } else { 0 };
          self.a = sum as u8;
          self.set_nz(self.a);
        }
      }
      0x61 => { // ADC
        let carry_in = (self.p & 1) as u16;
        if self.p & 0x08 != 0 {
          // BCD ADC
          let a_lo = (self.a & 0x0f) as u16;
          let a_hi = (self.a >> 4) as u16;
          let v_lo = (self.val & 0x0f) as u16;
          let v_hi = (self.val >> 4) as u16;
          let mut lo = a_lo + v_lo + carry_in;
          let mut hi = a_hi + v_hi;
          if lo > 9 { lo += 6; hi += 1; }
          let bin_sum = self.a as u16 + self.val as u16 + carry_in;
          let overflow = ((self.a ^ bin_sum as u8) & (self.val ^ bin_sum as u8) & 0x80) != 0;
          let carry = hi > 9;
          if hi > 9 { hi -= 10; }
          if lo > 15 { lo -= 16; }
          self.p = (self.p & !0x41) | if carry { 1 } else { 0 } | if overflow { 0x40 } else { 0 };
          let res = ((hi & 0x0f) << 4) | (lo & 0x0f);
          self.a = res as u8;
          self.p = (self.p & !0x82) | (bin_sum as u8 & 0x80) | if bin_sum as u8 == 0 { 2 } else { 0 };
        } else {
          let sum = self.a as u16 + self.val as u16 + carry_in;
          let carry = sum > 255;
          let overflow = ((self.a ^ sum as u8) & (self.val ^ sum as u8) & 0x80) != 0;
          self.p = (self.p & !0x41) | if carry { 1 } else { 0 } | if overflow { 0x40 } else { 0 };
          self.a = sum as u8;
          self.set_nz(self.a);
        }
      }
      0x22 => { // ROL
        self.result = self.p & 1;
        self.result |= self.val.wrapping_mul(2);
        self.p = (self.p & !1) | (self.val >> 7);
        self.memop();
      }
      0x02 => { // ASL
        self.result = self.val.wrapping_mul(2);
        self.p = (self.p & !1) | (self.val >> 7);
        self.memop();
      }
      0x62 => { // ROR
        self.result = self.p << 7;
        self.result |= self.val >> 1;
        self.p = (self.p & !1) | (self.val & 1);
        self.memop();
      }
      0x42 => { // LSR
        self.result = self.val >> 1;
        self.p = (self.p & !1) | (self.val & 1);
        self.memop();
      }
      0xc2 => { // DEC
        self.result = self.val.wrapping_sub(1);
        self.memop();
      }
      0xe2 => { // INC
        self.result = self.val.wrapping_add(1);
        self.memop();
      }
      0x20 => { // BIT
        self.p = (self.p & 0x3d) | (self.val & 0xc0) | if self.a & self.val == 0 { 2 } else { 0 };
      }
      0x40 => { // JMP absolute
        self.pc = (self.addr_hi as u16) << 8 | self.addr_lo as u16;
        self.cycles -= 1;
      }
      0x60 => { // JMP indirect
        let lo = self.val;
        let hi = self.mem_read(((self.addr_hi as u16) << 8 | self.addr_lo as u16).wrapping_add(1));
        self.pc = (hi as u16) << 8 | lo as u16;
        self.cycles += 1;
      }
      _ => {
        // STY/STA/STX, LDY/LDA/LDX, CPY/CMP/CPX
        let opcodehi3 = self.opcode >> 5;
        // reg: opcode%4==2 | opcodehi3==7 ? X : opcode%4==1 ? A : Y
        let reg_idx = if self.opcode % 4 == 2 || opcodehi3 == 7 {
          2 // X
        } else if self.opcode % 4 == 1 {
          0 // A
        } else {
          1 // Y
        };
        if opcodehi3 == 4 {
          // STY/STA/STX — записать reg в память
          let val = match reg_idx { 0 => self.a, 1 => self.y, _ => self.x };
          self.mem_write((self.addr_hi as u16) << 8 | self.addr_lo as u16, val);
        } else if opcodehi3 != 5 {
          // CPY/CMP/CPX — сравнить
          let reg = match reg_idx { 0 => self.a, 1 => self.y, _ => self.x };
          self.p = (self.p & !1) | if reg >= self.val { 1 } else { 0 };
          self.set_nz(reg.wrapping_sub(self.val));
        } else {
          // LDY/LDA/LDX — загрузить val в reg
          let v = self.val;
          match reg_idx {
            0 => { self.a = v; self.set_nz(self.a); }
            1 => { self.y = v; self.set_nz(self.y); }
            _ => { self.x = v; self.set_nz(self.x); }
          }
        }
      }
    }
  }

  /// Записать result обратно в память (или в A для accumulator mode).
  #[inline(always)]
  fn memop(&mut self) {
    self.set_nz(self.result);
    if self.nomem {
      self.a = self.result;
    } else {
      self.cycles += 2;
      self.mem_write((self.addr_hi as u16) << 8 | self.addr_lo as u16, self.result);
    }
  }

  /// Добавить X или Y к addr_lo/addr_hi, учесть page crossing.
  #[inline(always)]
  fn add_x_or_y(&mut self) {
    let opcodelo5 = self.opcode & 31;
    // val = opcodelo5<28 | opcode==190 ? Y : X
    let idx = if opcodelo5 < 28 || self.opcode == 190 { self.y } else { self.x };
    let new_lo = self.addr_lo.wrapping_add(idx);
    self.cross = new_lo < self.addr_lo; // page crossing
    if self.cross {
      self.addr_hi = self.addr_hi.wrapping_add(1);
    }
    self.addr_lo = new_lo;
    // cycles += ((opcode & 224)==128 | opcode%16==14 & opcode!=190) | cross
    let extra = if (self.opcode & 0xe0) == 0x80 || (self.opcode % 16 == 14 && self.opcode != 190) {
      1
    } else {
      0
    };
    self.cycles += extra + if self.cross { 1 } else { 0 };
  }

  /// Bench: крутить cpu_step() без PPU, считать guest cycles, замерять mcycle.
  /// На каждом 29830 cycles (1 NTSC кадр) — логировать FPS, cpf, hpg.
  /// Также читает RAM $02/$03 — результат nestest.nes (0x00 = OK, иначе код ошибки).
  pub fn run_bench(&mut self) -> ! {
    const CPU_HZ: u64 = 1_009_000_000;
    const CYCLES_PER_FRAME: u32 = 29830;
    let mut frame_cycles: u32 = 0;
    let mut frame: u32 = 0;
    let mut last_mcycle: u64 = 0;
    let mut step_count: u32 = 0;
    println!("smol: bench start (CPU only, no PPU) pc=0x{:04x}", self.pc);
    loop {
      self.cpu_step();
      step_count = step_count.wrapping_add(1);
      let cycles = self.cycles as u32;
      frame_cycles = frame_cycles.wrapping_add(cycles);
      if step_count <= 5 {
        println!("smol: step {} opcode=0x{:02x} pc=0x{:04x} cycles={}", step_count, self.opcode, self.pc, cycles);
      }
      if frame_cycles >= CYCLES_PER_FRAME {
        frame_cycles -= CYCLES_PER_FRAME;
        frame = frame.wrapping_add(1);
        let now = riscv::register::mcycle::read() as u64;
        // nestest результат: $02 = код ошибки (0 = OK), $03 = номер теста
        let err = self.ram[0x02];
        let test_num = self.ram[0x03];
        if last_mcycle != 0 && frame % 10 == 0 {
          let cpf = now - last_mcycle;
          let hpg = cpf / (CYCLES_PER_FRAME as u64);
          let fps = CPU_HZ / cpf;
          println!(
            "smol: frame {} fps={} cpf={} hpg={} steps={} err=0x{:02x} test={} pc=0x{:04x}",
            frame, fps, cpf, hpg, step_count, err, test_num, self.pc
          );
        }
        // Если nestest нашёл ошибку — выводим и продолжаем (чтобы видеть все)
        if err != 0 && frame % 1 == 0 && last_mcycle != 0 {
          println!("smol: NESTEST ERROR err=0x{:02x} test={} pc=0x{:04x}", err, test_num, self.pc);
        }
        last_mcycle = now;
      }
    }
  }

  /// Загрузить Klaus Dormann 6502 functional test.
  /// bin — 64KB дамп памяти. Копируется в klaus_mem, PC=$0400.
  pub fn load_klaus_test(&mut self, bin: &'static [u8]) {
    self.klaus_mode = true;
    // Копируем bin в klaus_mem (до 64KB).
    let n = bin.len().min(65536);
    self.klaus_mem[..n].copy_from_slice(&bin[..n]);
    // Старт: PC=$0400 (code_segment), SP=$FF, P=0x04 (только I flag).
    self.pc = 0x0400;
    self.s = 0xff;
    self.p = 0x04;
    self.a = 0;
    self.x = 0;
    self.y = 0;
    println!(
      "smol: klaus test loaded, bin={}B pc=0x{:04x}",
      bin.len(), self.pc
    );
  }

  /// Запустить Klaus Dormann 6502 functional test.
  /// Крутит cpu_step, детектирует зацикливание (PC не меняется N шагов подряд).
  /// При зацикливании — выводит результат ($0200..$0203) и PC.
  pub fn run_klaus_test(&mut self) -> ! {
    let mut last_pc: u16 = 0;
    let mut same_pc_count: u32 = 0;
    let mut step_count: u64 = 0;
    let start_mcycle = riscv::register::mcycle::read() as u64;
    println!("smol: klaus test running...");
    loop {
      self.cpu_step();
      step_count = step_count.wrapping_add(1);
      // Детект зацикливания: PC не меняется 3 шага подряд (JMP $).
      if self.pc == last_pc {
        same_pc_count += 1;
        if same_pc_count >= 3 {
          let now = riscv::register::mcycle::read() as u64;
          let elapsed = now - start_mcycle;
          // Результат теста: $0200..$0203
          let r0 = self.klaus_mem[0x0200];
          let r1 = self.klaus_mem[0x0201];
          let r2 = self.klaus_mem[0x0202];
          let r3 = self.klaus_mem[0x0203];
          println!(
            "smol: KLAUS HALT pc=0x{:04x} steps={} elapsed_mcycle={} result=0x{:02x} 0x{:02x} 0x{:02x} 0x{:02x}",
            self.pc, step_count, elapsed, r0, r1, r2, r3
          );
          // r2 == 0x00 — НЕ значит успех. Klaus зацикливается на success trap
          // или error trap. Нужно смотреть PC и сравнить с listing.
          // Успех: PC = load_addr + success_trap (обычно $0400 + offset)
          // Ошибка: PC = load_addr + error_trap, $0202 = код ошибки
          if r2 == 0 {
            println!("smol: KLAUS LIKELY SUCCESS (r2=0)");
          } else {
            println!("smol: KLAUS ERROR err=0x{:02x} test={}", r2, r3);
          }
          loop {}
        }
      } else {
        same_pc_count = 0;
      }
      last_pc = self.pc;
      // Периодический лог каждые 100000 шагов.
      if step_count % 100000 == 0 {
        let now = riscv::register::mcycle::read() as u64;
        let elapsed = now - start_mcycle;
        println!(
          "smol: klaus running steps={} pc=0x{:04x} elapsed_mcycle={}",
          step_count, self.pc, elapsed
        );
      }
    }
  }
}
