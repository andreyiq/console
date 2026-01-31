pub fn delay(n: u32) {
  for _ in 0..n {
    core::hint::spin_loop();
  }
}

pub fn set_bits(mut target_value: u32, value: u32, offset: u32, width: u32) {
  let mask = ((1 << width) - 1) << offset;
  let value_shifted = (value & ((1 << width) - 1)) << offset;
  target_value = (target_value & !mask) | value_shifted;
}

macro_rules! println {
  ($s:expr) => ({
    let message = $s; // Ожидается строка
    for byte in message.bytes() {
      uart::uart0_write(byte as u32);
    }
    uart::uart0_write(b'\n' as u32);
  });
}
