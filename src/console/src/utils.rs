//! Утилиты: задержка, печать в UART, битовые операции.

use crate::uart::uart0_write;

/// Зацикленная задержка. Грубо: n итераций spin_loop.
/// На ~1 GHz одна итерация ~1-2 такта, так что delay(1_000_000) ~ 1-2 ms.
pub fn delay(n: u32) {
  for _ in 0..n {
    core::hint::spin_loop();
  }
}

/// Вписывает `value` в `target_value` по смещению `offset`, ширина `width` бит.
/// Возвращает новое значение (не мутирует на месте).
pub fn set_bits(target_value: u32, value: u32, offset: u32, width: u32) -> u32 {
  let mask = ((1 << width) - 1) << offset;
  (target_value & !mask) | ((value & ((1 << width) - 1)) << offset)
}

/// Печатает u32 в hex виде: 0xDEADBEEF (без перевода строки).
pub fn print_hex(n: u32) {
  const HEX: &[u8] = b"0123456789ABCDEF";
  uart0_write(b'0' as u32);
  uart0_write(b'x' as u32);
  for i in (0..8).rev() {
    let b = ((n >> (i * 4)) & 0xF) as usize;
    uart0_write(HEX[b] as u32);
  }
  uart0_write(b' ' as u32);
}

/// Печатает строку без перевода строки.
macro_rules! print {
  ($s:expr) => {{
    let message = $s;
    for byte in message.bytes() {
      crate::uart::uart0_write(byte as u32);
    }
  }};
}

/// Печатает строку с переводом строки.
macro_rules! println {
  ($s:expr) => {{
    let message = $s;
    for byte in message.bytes() {
      crate::uart::uart0_write(byte as u32);
    }
    crate::uart::uart0_write(b'\n' as u32);
  }};
  ($fmt:expr, $($arg:tt)*) => {{
    use core::fmt::Write;
    let mut w = crate::uart::UartWriter;
    let _ = write!(w, $fmt, $($arg)*);
    let _ = writeln!(w);
  }};
}
