//use core::convert::TryInto;
//use core::ptr::{write_volatile, read_volatile};

// use crate::nes::{PIX_HEIGHT, PIX_WIDTH};
//use crate::{dma, spi};
//use crate::gpio;
use crate::utils::delay;

pub const WIDTH:  u16 = 480;
pub const HEIGHT: u16 = 320;

// FIXME
/*
fn spi_send(_data: &[u8]) {
  unsafe {
    for b in data {
      while (read_volatile(spi::SPI1_STATR) & spi::SPI1_STATR_TXE) == 0 {}
      write_volatile(spi::SPI1_DATAR, (*b) as u16);
      while (read_volatile(spi::SPI1_STATR) & spi::SPI1_STATR_BSY) != 0 {}
    }
  }
}
*/

pub fn write_command(_cmd: Command) {
  // FIXME
  /*
  unsafe {
    // RS
    // write_volatile(gpio::GPIOA_BCR, 1 << 3);
    spi_send(&[cmd as u8]);
  }
  */
}

pub fn write_data(_data: &[u8]) {
  // FIXME
  /*
  unsafe {
    // RS
    //write_volatile(gpio::GPIOA_BSHR, 1 << 3);
    spi_send(data);
  }
  */
}

pub fn write_command_data(cmd: Command, data: &[u8]) {
  write_command(cmd);
  write_data(data);
}

pub enum Command {
  SoftReset = 0x01,
  SleepOut = 0x11,
  NormalDisplayMode = 0x13,
  DisplayInversionOn = 0x21,
  AllPixelOn = 0x23,
  DisplayOn = 0x29,
  ColumnAddressSet = 0x2a,
  PageAddressSet = 0x2b,
  MemoryWrite = 0x2c,
  MemoryAccessControl = 0x36,
  InterfacePixelFormat = 0x3a,
  IdleModeOn = 0x39,
  FrameRateControl = 0xb1,
  InterfaceMode = 0xb0,
  DisplayInversionControl = 0xb4,
  PowerControl1 = 0xc0,
  PowerControl2 = 0xc1,
  VcomControl1 = 0xc5,
  PositiveGammaControl = 0xe0,
  NegativeGammaControl = 0xe1,
  SetImageFunction = 0xe9,
  AdjustControl3 = 0xf7,
}

pub fn init_display() {
  write_command_data(Command::AdjustControl3, &[0xa9, 0x51, 0x2c, 0x82]);

  write_command_data(Command::PowerControl1, &[0x0f, 0x0f]); //vgh = 5*vci   vgl = -3*vci
  write_command_data(Command::PowerControl2, &[0x47]);
  write_command_data(Command::VcomControl1, &[0x00, 0x4d, 0x80]);

  write_command_data(Command::FrameRateControl, &[0xb0, 0x11]);
  write_command_data(Command::DisplayInversionControl, &[0x02]);
  write_command_data(Command::MemoryAccessControl, &[0xe8]);

  //write_command_data(Command::InterfacePixelFormat, &[0x55]);
 //rgb565

  write_command(Command::DisplayInversionOn);
 //ips

  write_command_data(Command::SetImageFunction, &[0x00]);

  write_command_data(Command::AdjustControl3, &[0xa9, 0x51, 0x2c, 0x82]);

  write_command_data(Command::PositiveGammaControl, &[0x00, 0x07, 0x0b, 0x03, 0x0f, 0x05, 0x30, 0x56, 0x47, 0x04, 0x0b, 0x0a, 0x2d, 0x37, 0x0f]);
  write_command_data(Command::NegativeGammaControl, &[0x00, 0x0e, 0x13, 0x04, 0x11, 0x07, 0x39, 0x45, 0x50, 0x07, 0x10, 0x0d, 0x32, 0x36, 0x0f]);

  write_command(Command::SleepOut);
  delay(480_000);

  write_command(Command::DisplayOn);
  delay(80_000);
}

/* FIXME
#[inline(always)]
fn write_u16_be(val: u16) {
  write_data(&[(val >> 8) as u8,(val & 0xFF) as u8]);
}
*/

pub fn set_window(x: u16, y: u16, w: u16, h: u16) {
  let ox = x;
  let oy = y;
  write_command_data(Command::ColumnAddressSet, &[
    (ox >> 8) as u8,
    (ox & 0xff) as u8,
    ((ox  + w - 1) >> 8) as u8,
    ((ox  + w - 1) & 0xff) as u8,
  ]);

  write_command_data(Command::PageAddressSet, &[
    (oy >> 8) as u8,
    (oy & 0xff) as u8,
    ((oy + h - 1) >> 8) as u8,
    ((oy + h - 1) & 0xff) as u8,
  ]);
}

pub fn fill_screen_red(_frame_buf: &mut [u8], r: u8, g: u8, b: u8) {
  //set_window((WIDTH - PIX_WIDTH) / 2, (HEIGHT - PIX_HEIGHT) / 2, PIX_WIDTH, PIX_HEIGHT);
  set_window(0, 0, WIDTH, HEIGHT);
  //set_window(110, 190, 100, 100);

  //let data: [u8; 100*100*3] = [r; 100*100*100];

  // Memory write
  write_command(Command::MemoryWrite);
  // write_data(&img::IMG_DATA);

  for _ in 0..(WIDTH as usize * HEIGHT as usize){
    write_data(&[r, g, b]);
  }
}
