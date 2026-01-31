pub const GPIO_BASE: u32 = 0x0200_0000;

pub const PE_CFG0: *mut u32 = (GPIO_BASE + 0x00C0) as *mut u32;
pub const PE_CFG1: *mut u32 = (GPIO_BASE + 0x00C4) as *mut u32;
pub const PE_DAT: *mut u32 = (GPIO_BASE + 0x00D0) as *mut u32;
pub const PE_DRV0: *mut u32 = (GPIO_BASE + 0x00D4) as *mut u32;
pub const PE_DRV1: *mut u32 = (GPIO_BASE + 0x00D8) as *mut u32;
pub const PE_PULL0: *mut u32 = (GPIO_BASE + 0x00E4) as *mut u32;

#[repr(u32)]
pub enum Pull {
  UpDownDisabled = 0b00,
  Up = 0b01,
  Down = 0b10,
}
