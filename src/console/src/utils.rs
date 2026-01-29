pub fn delay(n: u32) {
  for _ in 0..n {
    core::hint::spin_loop();
  }
}
