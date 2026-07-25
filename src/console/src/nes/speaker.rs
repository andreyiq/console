//! Заглушка звука: APU крейта `runes` ждёт реализацию `Speaker`,
//! но у нас на F133 пока нет аудиовыхода. Просто игнорируем сэмплы.

use runes::apu;

pub struct NoAudio;

impl apu::Speaker for NoAudio {
  #[inline(always)]
  fn queue(&mut self, _sample: i16) {}
}
