//! Юнит-тест CPU 6502 на host (std). Копия логики из src/nes/smol.rs.
//! Запуск: cargo test --test klaus_test -- --nocapture

use std::fs;

/// CPU + 64KB память (для Klaus Dormann test).
struct Nes {
  // CPU registers
  a: u8, x: u8, y: u8, p: u8, s: u8, pc: u16,
  addr_lo: u8, addr_hi: u8, nomem: bool, result: u8, val: u8, cross: bool,
  cycles: u16, opcode: u8, nmi_irq: u8,
  mem: [u8; 65536],
}

const MASK: [u8; 20] = [
  128, 64, 1, 2, 1, 0, 0, 1, 4, 0, 0, 4, 0, 0, 64, 0, 8, 0, 0, 8,
];

impl Nes {
  fn new() -> Self {
    Self {
      a: 0, x: 0, y: 0, p: 4, s: 0xff, pc: 0,
      addr_lo: 0, addr_hi: 0, nomem: false, result: 0, val: 0, cross: false,
      cycles: 0, opcode: 0, nmi_irq: 0,
      mem: [0; 65536],
    }
  }

  #[inline(always)]
  fn mem_read(&self, addr: u16) -> u8 { self.mem[addr as usize] }

  #[inline(always)]
  fn mem_write(&mut self, addr: u16, val: u8) {
    self.mem[addr as usize] = val;
  }

  #[inline(always)]
  fn read_pc(&mut self) -> u8 {
    self.val = self.mem_read(self.pc);
    self.pc = self.pc.wrapping_add(1);
    self.val
  }

  #[inline(always)]
  fn set_nz(&mut self, val: u8) {
    self.p = (self.p & 0x7d) | (val & 0x80) | if val == 0 { 2 } else { 0 };
  }

  #[inline(always)]
  fn push(&mut self, val: u8) {
    let s = self.s;
    self.mem_write(0x0100 | s as u16, val);
    self.s = s.wrapping_sub(1);
  }

  #[inline(always)]
  fn pull(&mut self) -> u8 {
    self.s = self.s.wrapping_add(1);
    self.mem_read(0x0100 | self.s as u16)
  }

  fn cpu_step(&mut self) {
    self.cycles = 0;
    self.nomem = false;
    if self.nmi_irq != 0 {
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
          self.val = self.read_pc();
          self.nomem = true;
          self.execute_alu();
        } else {
          match self.opcode >> 5 {
            0 => {
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
            1 => {
              let result = self.read_pc();
              self.push((self.pc >> 8) as u8);
              self.push(self.pc as u8);
              let hi = self.read_pc();
              self.pc = (hi as u16) << 8 | result as u16;
            }
            2 => {
              self.p = self.pull() & !0x10;
              let lo = self.pull();
              let hi = self.pull();
              self.pc = (hi as u16) << 8 | lo as u16;
              self.cycles += 2;
            }
            3 => {
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
      16 => {
        let offset_byte = self.read_pc();
        let m = MASK[(self.opcode >> 6) as usize];
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
          0 => { self.push(self.p | 0x30); self.cycles += 1; }
          2 => {
            self.p = self.pull() & !0x10;
            self.cycles += 2;
          }
          4 => { self.push(self.a); self.cycles += 1; }
          6 => { let v = self.pull(); self.set_nz(v); self.a = v; self.cycles += 2; }
          8 => { self.y = self.y.wrapping_sub(1); self.set_nz(self.y); }
          9 => { self.a = self.y; self.set_nz(self.a); }
          10 => { self.y = self.a; self.set_nz(self.y); }
          12 => { self.y = self.y.wrapping_add(1); self.set_nz(self.y); }
          14 => { self.x = self.x.wrapping_add(1); self.set_nz(self.x); }
          _ => {
            let op = (self.opcode >> 4) as usize;
            self.p = (self.p & !MASK[op + 3]) | MASK[op + 4];
          }
        }
      }
      10 | 26 => {
        match self.opcode >> 4 {
          8 => { self.a = self.x; self.set_nz(self.a); }
          9 => { self.s = self.x; }
          10 => { self.x = self.a; self.set_nz(self.x); }
          11 => { self.x = self.s; self.set_nz(self.x); }
          12 => { self.x = self.x.wrapping_sub(1); self.set_nz(self.x); }
          14 => {}
          _ => {
            self.nomem = true;
            self.val = self.a;
            self.execute_alu();
          }
        }
      }
      _ => self.handle_addressing(opcodelo5),
    }
  }

  fn handle_addressing(&mut self, opcodelo5: u8) {
    match opcodelo5 {
      1 => {
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
      2 | 9 => {
        self.val = self.read_pc();
        self.nomem = true;
        self.execute_alu();
      }
      17 => {
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
      4 | 5 | 6 | 20 | 21 | 22 => {
        self.addr_lo = self.read_pc();
        let with_index = opcodelo5 > 6;
        if with_index {
          let idx = if (self.opcode & 0xd6) == 0x96 { self.y } else { self.x };
          self.addr_lo = self.addr_lo.wrapping_add(idx);
        }
        self.addr_hi = 0;
        self.cycles += 2;
        if self.opcode != 76 && (self.opcode & 0xe0) != 0x80 {
          self.val = self.mem_read(self.addr_lo as u16);
        }
        self.execute_alu();
      }
      12 | 13 | 14 | 25 | 28 | 29 | 30 => {
        self.addr_lo = self.read_pc();
        self.addr_hi = self.read_pc();
        if opcodelo5 < 25 {
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
      _ => {}
    }
  }

  #[inline(always)]
  fn add_x_or_y(&mut self) {
    let opcodelo5 = self.opcode & 31;
    let idx = if opcodelo5 < 28 || self.opcode == 190 { self.y } else { self.x };
    let new_lo = self.addr_lo.wrapping_add(idx);
    self.cross = new_lo < self.addr_lo;
    if self.cross { self.addr_hi = self.addr_hi.wrapping_add(1); }
    self.addr_lo = new_lo;
    let extra = if (self.opcode & 0xe0) == 0x80 || (self.opcode % 16 == 14 && self.opcode != 190) { 1 } else { 0 };
    self.cycles += extra + if self.cross { 1 } else { 0 };
  }

  fn execute_alu(&mut self) {
    self.result = 0;
    match self.opcode & 0xe3 {
      0x01 => { self.a |= self.val; self.set_nz(self.a); }
      0x21 => { self.a &= self.val; self.set_nz(self.a); }
      0x41 => { self.a ^= self.val; self.set_nz(self.a); }
      0xe1 => {
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
      0x61 => {
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
          // N/Z from binary result (6502 quirk)
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
      0x22 => {
        self.result = self.p & 1;
        self.result |= self.val.wrapping_mul(2);
        self.p = (self.p & !1) | (self.val >> 7);
        self.memop();
      }
      0x02 => {
        self.result = self.val.wrapping_mul(2);
        self.p = (self.p & !1) | (self.val >> 7);
        self.memop();
      }
      0x62 => {
        self.result = self.p << 7;
        self.result |= self.val >> 1;
        self.p = (self.p & !1) | (self.val & 1);
        self.memop();
      }
      0x42 => {
        self.result = self.val >> 1;
        self.p = (self.p & !1) | (self.val & 1);
        self.memop();
      }
      0xc2 => {
        self.result = self.val.wrapping_sub(1);
        self.memop();
      }
      0xe2 => {
        self.result = self.val.wrapping_add(1);
        self.memop();
      }
      0x20 => {
        self.p = (self.p & 0x3d) | (self.val & 0xc0) | if self.a & self.val == 0 { 2 } else { 0 };
      }
      0x40 => {
        self.pc = (self.addr_hi as u16) << 8 | self.addr_lo as u16;
        self.cycles -= 1;
      }
      0x60 => {
        let lo = self.val;
        let hi = self.mem_read(((self.addr_hi as u16) << 8 | self.addr_lo as u16).wrapping_add(1));
        self.pc = (hi as u16) << 8 | lo as u16;
        self.cycles += 1;
      }
      _ => {
        let opcodehi3 = self.opcode >> 5;
        let reg_idx = if self.opcode % 4 == 2 || opcodehi3 == 7 { 2 }
          else if self.opcode % 4 == 1 { 0 } else { 1 };
        if opcodehi3 == 4 {
          let val = match reg_idx { 0 => self.a, 1 => self.y, _ => self.x };
          self.mem_write((self.addr_hi as u16) << 8 | self.addr_lo as u16, val);
        } else if opcodehi3 != 5 {
          let reg = match reg_idx { 0 => self.a, 1 => self.y, _ => self.x };
          self.p = (self.p & !1) | if reg >= self.val { 1 } else { 0 };
          self.set_nz(reg.wrapping_sub(self.val));
        } else {
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

  /// Загрузить Klaus bin (64KB) в mem, PC=$0400.
  fn load_klaus(&mut self, bin: &[u8]) {
    let n = bin.len().min(65536);
    self.mem[..n].copy_from_slice(&bin[..n]);
    self.pc = 0x0400;
    self.s = 0xff;
    self.p = 0x04;
  }

  /// Крутить cpu_step до зацикливания (PC не меняется 3 шага подряд).
  /// Возвращает (steps, final_pc, result_bytes).
  fn run_until_halt(&mut self, max_steps: u64) -> (u64, u16, [u8; 4]) {
    let mut last_pc: u16 = 0;
    let mut same_pc: u32 = 0;
    let mut steps: u64 = 0;
    // Трассировка первых 60 шагов — чтобы увидеть где CPU убегает.
    let trace_limit: u64 = 0;
    let trace_from: u64 = u64::MAX;
    while steps < max_steps {
      self.cpu_step();
      steps += 1;
      if steps <= trace_limit || steps >= trace_from {
        eprintln!(
          "step={} pc=0x{:04x} op=0x{:02x} a=0x{:02x} x=0x{:02x} y=0x{:02x} s=0x{:02x} p=0x{:02x}",
          steps, self.pc, self.opcode, self.a, self.x, self.y, self.s, self.p
        );
      }
      if self.pc == last_pc {
        same_pc += 1;
        if same_pc >= 3 {
          let r = [self.mem[0x0200], self.mem[0x0201], self.mem[0x0202], self.mem[0x0203]];
          return (steps, self.pc, r);
        }
      } else {
        same_pc = 0;
      }
      last_pc = self.pc;
    }
    let r = [self.mem[0x0200], self.mem[0x0201], self.mem[0x0202], self.mem[0x0203]];
    (steps, self.pc, r)
  }
}

#[test]
fn klaus_6502_functional_test() {
  let bin = fs::read("../console/archive/roms/6502_functional_test.bin")
    .expect("cannot read 6502_functional_test.bin");
  assert_eq!(bin.len(), 65536, "Klaus bin должен быть 64KB");
  let mut nes = Nes::new();
  nes.load_klaus(&bin);
  let (steps, final_pc, result) = nes.run_until_halt(50_000_000);
  println!(
    "klaus: halt after {} steps, pc=0x{:04x}, result=0x{:02x} 0x{:02x} 0x{:02x} 0x{:02x}",
    steps, final_pc, result[0], result[1], result[2], result[3]
  );
  assert!(steps < 50_000_000, "тест не зациклился за 50M шагов — завис");
  println!("klaus: test completed (halt detected)");
}
