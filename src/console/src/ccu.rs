pub const CCU_BASE: u32 = 0x0200_1000;

pub const UART_BGR_REG: *mut u32 = (CCU_BASE + 0x090C) as *mut u32;
// Сброс UART0
pub const UART0_RST: u32 = 1 << 16;
// Тактирование UART0
pub const UART0_GATING: u32 = 1;
