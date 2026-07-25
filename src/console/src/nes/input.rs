//! Ввод: настоящих кнопок пока нет, поэтому здесь автонажатие START.
//!
//! Зачем: без ввода Mario стоит на заставке и уходит в демо-режим не сразу, а
//! до этого APU молчит — на замере это выглядело как `peak=1`, то есть полная
//! тишина, и было не отличить от неработающего звука. Автостарт снимает этот
//! вопрос: игра начинается сама и сразу играет музыку.
//!
//! Позже сюда придёт опрос реальных кнопок по GPIO.

use core::sync::atomic::{AtomicU32, Ordering};

use runes::controller::stdctl;
use runes::controller::InputPoller;

/// Счётчик кадров — его двигает главный цикл через `tick_frame()`.
static FRAME: AtomicU32 = AtomicU32::new(0);

/// С какого кадра начинать «жать» START и сколько кадров держать.
///
/// Ждём 120 кадров, чтобы игра успела инициализироваться и показать заставку,
/// потом держим START 10 кадров — этого хватает, опрос идёт каждый кадр.
const START_AT: u32 = 120;
const START_LEN: u32 = 10;

/// Отметить, что прошёл кадр. Зовётся из главного цикла.
pub fn tick_frame() {
  FRAME.fetch_add(1, Ordering::Relaxed);
}

pub struct AutoStart;

impl InputPoller for AutoStart {
  #[inline(always)]
  fn poll(&self) -> u8 {
    let f = FRAME.load(Ordering::Relaxed);
    if f >= START_AT && f < START_AT + START_LEN {
      stdctl::START
    } else {
      0
    }
  }
}

/// Заглушка «все кнопки отпущены». Оставлена для сравнения.
#[allow(dead_code)]
pub struct NoInput;

impl InputPoller for NoInput {
  #[inline(always)]
  fn poll(&self) -> u8 {
    0
  }
}
