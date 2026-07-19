//! Заглушка ввода: геймпад NES не подключён, всегда возвращаем «все кнопки отпущены».
//! Позже сюда придёт GPIO-опрос реальных кнопок.

use runes::controller::InputPoller;

pub struct NoInput;

impl InputPoller for NoInput {
  #[inline(always)]
  fn poll(&self) -> u8 {
    0
  }
}
