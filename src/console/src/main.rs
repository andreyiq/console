#![no_std]
#![no_main]

use core::convert::TryInto;
use core::mem::transmute;

#[macro_use]
pub mod utils;
pub mod ccu;
pub mod gpio;
pub mod uart;
pub mod dma;
pub mod ili9844;
pub mod nes;

use runes::apu::APU;
use runes::cartridge::{MirrorType};
use runes::controller::stdctl;
use runes::mapper;
use runes::memory::{CPUMemory, PPUMemory};
use runes::mos6502;
use runes::ppu;
use nes::{SimpleCart, Screen, Speaker, INesHeader, ROM, Mapper2, Joystick};
use uart::{init_uart0, uart0_write};
use utils::delay;

//pub const GPIO_BASE: *mut u32 = 0x0200_0000 as *mut u32;
//pub const GPIO_BASE: usize = 0x0200_0000;
//pub const GPIO_PE_CFG0: usize = GPIO_BASE + 0x00C0;
pub const GPIO_PE_CFG0: *mut u32 = 0x0200_00C0 as *mut u32;
pub const GPIO_PE_DAT: *mut u32 = 0x0200_00D0 as *mut u32;
pub const PE0_OUTPUT: u32 = 0b0001;

use core::panic::PanicInfo;

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
  loop {}
}

fn init_display() {
}

#[riscv_rt::entry]
fn main() -> ! {
  //init_uart0();
  delay(100_000_000);
  println!("Hello world!!!");

  loop {
    delay(10_000_000);
  }

  /*
  unsafe {
    let mut cfg_value = GPIO_PE_CFG0.read_volatile();
    cfg_value &= 0b0000 & (0b0000 << 8);
    cfg_value |= PE0_OUTPUT;
    cfg_value |= PE0_OUTPUT << 8;
    GPIO_PE_CFG0.write_volatile(cfg_value);

    loop {
      GPIO_PE_DAT.write_volatile(0xFFFFFFFF);
      delay(1_000_0000);
      GPIO_PE_DAT.write_volatile(0x0);
      delay(1_000_0000);
    }
  }
  */

  init_display();


  let mut frame_buf: [u8; (nes::PIX_WIDTH * 3) as usize] = [0; (nes::PIX_WIDTH * 3) as usize];

  let mut pos = 0;
  let rheader: &[u8; 16] = &ROM[pos..(pos + 16)].try_into().unwrap();
  pos += 16;
  let header = unsafe { transmute::<[u8; 16], INesHeader>(*rheader) };
  let mirror = match ((header.flags6 >> 2) & 2) | (header.flags6 & 1) {
    0 => MirrorType::Horizontal,
    1 => MirrorType::Vertical,
    2 => MirrorType::Single0,
    3 => MirrorType::Single1,
    _ => MirrorType::Four,
  };
  let _mapper_id = (header.flags7 & 0xf0) | (header.flags6 >> 4);
  if header.flags6 & 0x04 == 0x04 {
    pos += 512;
  }

  let prg_len = header.prg_rom_nbanks as usize * 0x4000;
  let mut chr_len = header.chr_rom_nbanks as usize * 0x2000;
  if chr_len == 0 {
    chr_len = 0x2000;
  }

  let prg_pos = pos;
  let chr_pos = pos + prg_len;
  let mut sram = [0; 0x2000];

  let mut spkr = Speaker{};
  let mut win = Screen::new(&mut frame_buf);

  let cart = SimpleCart::new(&ROM, chr_pos, chr_len, prg_pos, prg_len, &mut sram, mirror);
  let mut m = Mapper2::new(cart);
  let mapper = mapper::RefMapper::new(&mut m);

  let event = Joystick::new();
  let p1ctl = stdctl::Joystick::new(&event);

  let mut cpu = mos6502::CPU::new(CPUMemory::new(&mapper, Some(&p1ctl), None));
  let mut ppu = ppu::PPU::new(PPUMemory::new(&mapper), &mut win);
  let mut apu = APU::new(&mut spkr);
  let cpu_ptr = &mut cpu as *mut mos6502::CPU;
  cpu.mem.bus.attach(cpu_ptr, &mut ppu, &mut apu);

  cpu.powerup();

  /*
  delay(100_000);
  unsafe {
    write_volatile(dma::DMA1_CFGR3, dma::DMA1_CFGR3_EN);
  }
  */

  /*
  for i in (0..frame_buf.len()).step_by(3) {
    frame_buf[i] = 0xff;
    frame_buf[i + 1] = 0x00;
    frame_buf[i + 2] = 0x00;
  }
  */

  /*
  ili9844::set_window(0, 0, ili9844::WIDTH, ili9844::HEIGHT);
  ili9844::write_command(ili9844::Command::MemoryWrite);
  unsafe {
    // write data
    write_volatile(gpio::GPIOA_BSHR, 1 << 3);
  }
  */
  //dma_enabled();

  loop {
    //ili9844::write_data(&frame_buf);

    /*
    unsafe {
      //while read_volatile(dma::DMA1_CFGR3) & dma::DMA1_CFGR3_EN != 0 {}
      for i in (0..frame_buf.len()).step_by(3) {
        frame_buf[i] = 0xff;
        frame_buf[i + 1] = 0x00;
        frame_buf[i + 2] = 0x00;
      }
      for _ in 0..(ili9844::HEIGHT / 5) {
        dma_send(&frame_buf);
        /*
        while read_volatile(dma::DMA1_CNTR3) != 0 {}
        write_volatile(dma::DMA1_CNTR3, frame_buf.len() as u32);
        */
      }
      for i in (0..frame_buf.len()).step_by(3) {
        frame_buf[i] = 0x00;
        frame_buf[i + 1] = 0xff;
        frame_buf[i + 2] = 0x00;
      }
      for _ in 0..(ili9844::HEIGHT / 5) {
        dma_send(&frame_buf);
      }
      for i in (0..frame_buf.len()).step_by(3) {
        frame_buf[i] = 0xff;
        frame_buf[i + 1] = 0x00;
        frame_buf[i + 2] = 0xff;
      }
      for _ in 0..(ili9844::HEIGHT / 5) {
        dma_send(&frame_buf);
      }
      /*
      for i in (0..frame_buf.len()).step_by(3) {
        frame_buf[i] = 0x00;
        frame_buf[i + 1] = 0xff;
        frame_buf[i + 2] = 0x00;
      }
      for _ in 0..(ili9844::HEIGHT / 5) {
        while read_volatile(dma::DMA1_CNTR3) != 0 {}
        write_volatile(dma::DMA1_CNTR3, frame_buf.len() as u32);
      }
      for i in (0..frame_buf.len()).step_by(3) {
        frame_buf[i] = 0x00;
        frame_buf[i + 1] = 0x00;
        frame_buf[i + 2] = 0xff;
      }
      for _ in 0..(ili9844::HEIGHT / 5) {
        while read_volatile(dma::DMA1_CNTR3) != 0 {}
        write_volatile(dma::DMA1_CNTR3, frame_buf.len() as u32);
      }
      */
    }*/
    //dma_enabled();
    //dma_enabled();

    /*
    ili9844::fill_screen_red(&mut frame_buf, 0xff, 0x00, 0x00);
    ili9844::fill_screen_red(&mut frame_buf, 0x00, 0xff, 0x00);
    ili9844::fill_screen_red(&mut frame_buf, 0x00, 0x00, 0xff);
    */
    /* consume the leftover cycles from the last instruction */
    while cpu.cycle > 0 {
      cpu.mem.bus.tick()
    }

    //print_cpu_trace(&cpu);
    //unsafe { GPIO_PE_DAT.write_volatile(0xFFFFFFFF); }
    cpu.step();
    //unsafe { GPIO_PE_DAT.write_volatile(0x0); }
    /*
    unsafe {
      ili9844::fill_screen_red(0xff, 0x00, 0x00);
    }
    */
    //println!("step");
  }
}
