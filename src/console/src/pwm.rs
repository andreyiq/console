//! PWM для F133 (раздел 9.10 user manual). Нужен для звука: NES APU отдаёт
//! сэмплы, мы превращаем их в скважность, а RC-фильтр (или сама катушка
//! динамика) обратно в аналоговый сигнал.
//!
//! Используем канал PWM7 — он выведен на PD22, а тот на гребёнку P2 пин 19
//! (единственный PWM, подписанный на официальной картинке разводки MangoPi).
//!
//! Как считается выход (9.10.3.5):
//! ```text
//! период  = (PWM_ENTIRE_CYCLE + 1) тактов PWM-клока
//! high    = PWM_ACT_CYCLE тактов        (при ACT_STA = 1)
//! ```
//! Берём ENTIRE_CYCLE = 255, то есть 256 тактов на период и ровно 8 бит
//! скважности — как раз старший байт сэмпла.
//!
//! Клок: APB0 без деления. Несущая = APB0 / 256. При APB0 100 МГц это
//! 390 кГц — далеко за пределами слышимого и заметно выше частоты сэмплов
//! 44.1 кГц, так что на один сэмпл приходится ~9 периодов несущей.

use core::ptr::{read_volatile, write_volatile};

use crate::ccu;
use crate::gpio::{Func, PD22};

pub const PWM_BASE: u32 = 0x0200_0C00;

/// Тактирование пары PWM6+PWM7: биты 8:7 — источник (00 HOSC, 01 APB0),
/// биты 3:0 — делитель M (0000 = /1).
const PCCR67: *mut u32 = (PWM_BASE + 0x002C) as *mut u32;
/// Гейтинг клока по каналам: биты 0..7 — PWM0..PWM7.
const PCGR: *mut u32 = (PWM_BASE + 0x0040) as *mut u32;
/// Разрешение выхода по каналам: биты 0..7.
const PER: *mut u32 = (PWM_BASE + 0x0080) as *mut u32;

/// Регистры канала N лежат по 0x0100 + N*0x20.
const fn ch(n: u32, off: u32) -> *mut u32 {
  (PWM_BASE + 0x0100 + n * 0x20 + off) as *mut u32
}

/// Канал звука. PWM7 → PD22 → гребёнка P2, пин 19.
const CH: u32 = 7;
/// Control: бит 8 ACT_STA (1 = активный уровень высокий), биты 7:0 PRESCAL_K.
const PCR: *mut u32 = ch(CH, 0x00);
/// Period: биты 31:16 ENTIRE_CYCLE, биты 15:0 ACT_CYCLE (скважность).
const PPR: *mut u32 = ch(CH, 0x04);

/// Период в тактах минус 1. 255 → 256 тактов → ровно 8 бит скважности.
const ENTIRE_CYCLE: u32 = 255;

/// Включить PWM7 на PD22 и начать выдавать середину шкалы (тишина).
///
/// Середина, а не ноль: сигнал у нас двуполярный (сэмпл i16 вокруг нуля),
/// поэтому тишина — это 50% скважности. Начинать с 0% нельзя, иначе при
/// первом же сэмпле динамик получит скачок.
pub fn init() {
  ccu::Peripheral::Pwm.enable();
  PD22.set_func(Func::Pwm7);

  unsafe {
    // Источник APB0 (01), делитель /1.
    write_volatile(PCCR67, 0b01 << 7);
    // Пропустить клок в канал.
    let v = read_volatile(PCGR);
    write_volatile(PCGR, v | (1 << CH));
    // Активный уровень высокий, предделитель 1, режим циклический.
    write_volatile(PCR, 1 << 8);
    // Период + стартовая скважность 50%.
    write_volatile(PPR, (ENTIRE_CYCLE << 16) | 128);
    // Разрешить выход.
    let v = read_volatile(PER);
    write_volatile(PER, v | (1 << CH));
  }
}

/// Задать скважность, 0..255. 128 — тишина.
///
/// Регистр периода кэшируется аппаратно: значение подхватывается на границе
/// периода, так что писать можно в любой момент без всплесков на выходе
/// (9.10.3.4, «cache load»).
#[inline(always)]
pub fn set_duty(duty: u8) {
  unsafe {
    write_volatile(PPR, (ENTIRE_CYCLE << 16) | duty as u32);
  }
}

/// Выключить звук — вернуть выход в середину шкалы.
pub fn silence() {
  set_duty(128);
}

/// Подождать `ms` миллисекунд по `mcycle` — до старта эмулятора других часов
/// у нас нет.
fn wait_ms(ms: u32) {
  let total = crate::utils::CPU_HZ * ms as u64 / 1000;
  let start = riscv::register::mcycle::read64();
  while riscv::register::mcycle::read64() - start < total {
    core::hint::spin_loop();
  }
}

/// Помигать синим светодиодом DS2 через PWM7.
///
/// На PD22 висит DS2: VCC3V3 → светодиод → 5.1К → PD22. То есть пин в нуле
/// (скважность 0) зажигает диод, пин в единице (255) гасит. Это проверка
/// глазами, что канал PWM реально доходит до ножки — без анализатора и без
/// зависимости от того, слышно ли что-то в динамике.
pub fn led_test(cycles: u32) {
  for _ in 0..cycles {
    set_duty(0); // пин в нуле → диод горит
    wait_ms(400);
    set_duty(255); // пин в единице → диод погашен
    wait_ms(400);
  }
  silence();
}

/// Меандр заданной частоты на заданное время — проверка, что провода целы.
///
/// Меандр, а не синус: цель — услышать хоть что-то и убедиться, что цепочка
/// PWM → пин → резистор → динамик работает.
///
/// Размах полный (0..255): на резисторе 470 Ом динамик получает всего
/// 8/478 от напряжения, поэтому урезать амплитуду тут нечего — нужен каждый
/// децибел. Для пина это безопасно, ток ограничен внешним резистором.
pub fn beep(freq_hz: u32, ms: u32) {
  let half = crate::utils::CPU_HZ / (2 * freq_hz as u64); // такт на полпериода
  let total = crate::utils::CPU_HZ * ms as u64 / 1000;
  let start = riscv::register::mcycle::read64();
  let mut next = start;
  let mut high = false;
  while riscv::register::mcycle::read64() - start < total {
    if riscv::register::mcycle::read64() >= next {
      high = !high;
      set_duty(if high { 255 } else { 0 });
      next += half;
    }
  }
  silence();
}

/// Текущее состояние регистров (для отладки).
pub fn read_regs() -> (u32, u32, u32, u32) {
  unsafe {
    (
      read_volatile(PCCR67),
      read_volatile(PCGR),
      read_volatile(PER),
      read_volatile(PPR),
    )
  }
}
