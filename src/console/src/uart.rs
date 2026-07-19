use crate::{ccu, gpio, utils::set_bits};

pub const UART0_BASE: u32 = 0x0250_0000;

pub const UART_THR: *mut u32 = (UART0_BASE + 0x0000) as *mut u32;
pub const UART_DLL: *mut u32 = (UART0_BASE + 0x0000) as *mut u32;
pub const UART_DLH: *mut u32 = (UART0_BASE + 0x0004) as *mut u32;
pub const UART_FCR: *mut u32 = (UART0_BASE + 0x0008) as *mut u32;
pub const UART_USR: *mut u32 = (UART0_BASE + 0x007C) as *mut u32;
pub const UART_HALT: *mut u32 = (UART0_BASE + 0x00A4) as *mut u32;
pub const UART_LCR: *mut u32 = (UART0_BASE + 0x000C) as *mut u32;

pub fn init_uart0() {
  unsafe {
    // Включаем тактирование
    let mut v = ccu::UART_BGR_REG.read_volatile();
    v |= ccu::UART0_GATING | ccu::UART0_RST;
    ccu::UART_BGR_REG.write(v);

    // Настраиваем порты
    let mut v = gpio::PE_CFG0.read_volatile();
    // Function 6 = UART0, 8 - PE2, 12 - PE3
    set_bits(v, 6, 8, 4);
    set_bits(v, 6, 12, 4);
    gpio::PE_CFG0.write_volatile(v);

    let mut v = gpio::PE_PULL0.read_volatile();
    // 4 - PE2, 6 - PE3
    set_bits(v, gpio::Pull::Up as u32, 4, 2);
    set_bits(v, gpio::Pull::Up as u32, 6, 2);
    gpio::PE_PULL0.write_volatile(v);

    // Baud 115200 при 24 MHz: divisor = 24000000/(16*115200) = 13
    UART_FCR.write_volatile(1);
    UART_HALT.write_volatile(1);
    UART_LCR.write_volatile(0x1 << 7); // DLAB=1
    UART_DLL.write_volatile(13); // divisor low
    UART_DLH.write_volatile(0); // divisor high
    UART_LCR.write_volatile(0x03); // DLAB=0, 8N1
                                   // Set UART_HALT[HALT_TX] to 0 to enable TX transfer.
    let mut v = UART_HALT.read_volatile();
    v &= !1;
    UART_HALT.write_volatile(v);

    // Step 3 Controller Parameter Configuration
    // Set data width, stop bits, and even/odd parity type by writing the UART_LCR register.
    // Reset, enable FIFO and set FIFO trigger condition by writing the UART_FCR register.
    // Set the flow control parameter by writing the UART_MCR register.
  }
}

pub fn uart0_write(v: u32) {
  unsafe {
    UART_THR.write_volatile(v);
    // FIXME Step 2 Check TX_FIFO status by reading UART_USR[TFNF]. If the bit is 1, data can continue to be written; if the bit is 0, wait for data transfer, and data cannot continue to write until FIFO is not full.
    while (UART_USR.read_volatile() & 0b10) == 0 {}
  }
}
