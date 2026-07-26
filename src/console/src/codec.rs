//! Аудиокодек F133 — настоящий 16-битный DAC вместо PWM. Раздел 8.4 UM.
//!
//! # Зачем он, если звук уже был
//!
//! PWM-выход это один бит, размазанный по времени: 8 бит громкости получаются
//! только усреднением несущей 390 кГц, шумовая полка ~48 дБ, и всё это едет
//! прямо на ножку GPIO, откуда динамик через 470 Ом получает доли милливатта.
//! Кодек — отдельный аналоговый блок: 100 дБ SNR по паспорту, свой ЦАП,
//! усилитель наушников на выходе (HPOUTL/HPOUTR, пины 98/99).
//!
//! На MangoPi MQ v1.6 эти пины идут через развязку 10 мкФ (C54/C15) и
//! резисторы 150К (R20/R21) на вход усилителя PAM8301 (U10), а его выход —
//! это гребёнка P6 «AUDIO». То есть динамик, припаянный к двум пинам P6,
//! получает ~0.1 Вт от штатного класс-D усилителя. Ничего паять больше не
//! нужно — вся цепочка уже на плате, надо только включить кодек.
//!
//! # Тракт
//!
//! ```text
//! наши сэмплы → AC_DAC_TXDATA → FIFO 128×20 бит → DAC → усилитель наушников
//!   → HPOUTL/R → C54/C15 → R20/R21 → PAM8301 → P6 (динамик)
//! ```
//!
//! # Тактирование
//!
//! PLL_AUDIO1 = 24 МГц × 128 = 3072 МГц, выход DIV5 = 614.4 МГц. Делим на 25 —
//! получаем 24.576 МГц, штатную частоту семейства 48 кГц. Дальше сам кодек
//! делит её по полю `DAC_FS`; мы берём 24 кГц (`DAC_FS=010`).
//!
//! Почему 24 кГц, а не 48: FIFO это 128 сэмплов, то есть запас по времени
//! равен 128/Fs. На 24 кГц это 5.3 мс, на 48 кГц — 2.7 мс. Между вызовами
//! `pump()` у нас бывают паузы под 6 мс (DMA-флаш кадра, печать в UART), и на
//! 48 кГц FIFO успевал бы опустеть. Для NES 24 кГц с запасом: у APU нет
//! ничего выше 12 кГц.
//!
//! # Темп задаёт железо
//!
//! Главное отличие от PWM: сетку отсчётов держит кодек, а не наш код. Раньше
//! `pump()` выставлял скважность «по `mcycle`», и любое дрожание вызова было
//! слышно как шум. Теперь мы только подкладываем сэмплы в FIFO, пока в нём
//! есть место; когда именно мы это сделали — неважно, лишь бы FIFO не
//! опустел. Плюс `SEND_LASAT`: при опустошении кодек повторяет последний
//! сэмпл, а не выдаёт ноль, — провал слышен как заминка, а не как щелчок.

use core::ptr::{read_volatile, write_volatile};
use core::sync::atomic::{AtomicBool, Ordering};

use crate::ccu::CCU_BASE;
use crate::utils::delay;

pub const CODEC_BASE: u32 = 0x0203_0000;

/// Цифровая часть DAC.
const AC_DAC_DPC: *mut u32 = (CODEC_BASE + 0x0000) as *mut u32;
const DAC_VOL_CTRL: *mut u32 = (CODEC_BASE + 0x0004) as *mut u32;
const AC_DAC_FIFOC: *mut u32 = (CODEC_BASE + 0x0010) as *mut u32;
const AC_DAC_FIFOS: *mut u32 = (CODEC_BASE + 0x0014) as *mut u32;
const AC_DAC_TXDATA: *mut u32 = (CODEC_BASE + 0x0020) as *mut u32;
/// Аналоговая часть: ЦАП и LINEOUT.
const DAC_REG: *mut u32 = (CODEC_BASE + 0x0310) as *mut u32;
/// Аналоговая часть: генератор плавного нарастания (ramp).
///
/// Он же задаёт среднюю точку выхода наушников: в `HP2_REG` поле `RSWITCH`
/// выбирает, брать VCM от ramp-ЦАПа (0) или от VRA1 (1), и рабочая
/// конфигурация — первая. Значит блок должен быть включён, иначе выход стоит
/// без опоры и на ножках тишина. Это и есть «Maaagic...» из драйвера
/// `sun20i-codec.c`: `RD_EN` поднять, `RMC_EN` (ручное управление) опустить.
const RAMP_REG: *mut u32 = (CODEC_BASE + 0x031C) as *mut u32;
/// Регистр 0x0324, в мануале v1.0 не описан вообще.
///
/// На диаграмме тракта (Figure 8-28) путь DACL → HPOUTL подписан тремя
/// битами: `Reg000[31]`, `Reg310[15]` и `Reg324[15]`, а текст 8.4.3.8 говорит
/// «The headphone PA is powered up or down by HP_REG[bit15] (HPPA_EN)».
/// Референсный драйвер его не трогает вовсе, поэтому и мы не трогаем — только
/// печатаем в лог, чтобы видеть значение по сбросу. Тот же абзац мануала
/// упоминает charge pump и работу без развязочных конденсаторов, чего у F133
/// нет, — похоже, абзац и подпись на схеме перенесены из другого чипа.
const HP1_REG: *mut u32 = (CODEC_BASE + 0x0324) as *mut u32;
/// Аналоговая часть: усилитель наушников.
const HP2_REG: *mut u32 = (CODEC_BASE + 0x0340) as *mut u32;
/// Аналоговая часть: внутренние LDO (AVCC и HPVCC питаются отсюда).
const POWER_REG: *mut u32 = (CODEC_BASE + 0x0348) as *mut u32;

const PLL_AUDIO1_CTRL_REG: *mut u32 = (CCU_BASE + 0x0080) as *mut u32;
const AUDIO_CODEC_DAC_CLK_REG: *mut u32 = (CCU_BASE + 0x0A50) as *mut u32;
const AUDIO_CODEC_BGR_REG: *mut u32 = (CCU_BASE + 0x0A5C) as *mut u32;

/// Частота дискретизации кодека. Задаётся полем `DAC_FS` (010 = 24 кГц) при
/// модульном такте 24.576 МГц.
pub const SAMPLE_HZ: u32 = 24_000;

/// Глубина TX FIFO в сэмплах. 128 в моно-режиме (в стерео было бы 64).
pub const FIFO_DEPTH: u32 = 128;

/// PLL_AUDIO1 без бита включения: N=128 (0x7F+1), M=1, P0=2, P1=5.
/// 24 МГц × 128 = 3072 МГц, DIV5 = 614.4 МГц.
///
/// Пишем явно, а не полагаемся на дефолт: до нас в этом регистре мог
/// похозяйничать BROM или payload xfel.
const PLL_AUDIO1_CFG: u32 = (1 << 30) // PLL_LDO_EN
  | (1 << 27) // PLL_OUTPUT_GATE
  | (4 << 20) // P1 = 4+1 = 5
  | (1 << 16) // P0 = 1+1 = 2
  | (0x7F << 8); // N = 0x7F+1 = 128

const PLL_EN: u32 = 1 << 31;
const PLL_LOCK_ENABLE: u32 = 1 << 29;
const PLL_LOCK: u32 = 1 << 28;

/// Модульный такт DAC: gating | источник PLL_AUDIO1(DIV5) | N=/1 | M=25.
/// 614.4 МГц / 25 = 24.576 МГц.
const DAC_CLK_CFG: u32 = (1 << 31) | (0b010 << 24) | (0b00 << 8) | (25 - 1);

/// Настройка FIFO: 24 кГц, моно, 16 бит, при опустошении повторять последний
/// сэмпл. Прерывания и DRQ не нужны — мы кладём сэмплы опросом.
const FIFOC_CFG: u32 = (0b010 << 29) // DAC_FS = 24 кГц
  | (1 << 26) // SEND_LASAT: при underrun повторять последний сэмпл
  | (0b01 << 24) // FIFO_MODE: 16-битный сэмпл лежит в TXDATA[15:0]
  | (0x40 << 8) // TX_TRIG_LEVEL, дефолт (без DRQ/IRQ роли не играет)
  | (1 << 6); // DAC_MONO_EN: один поток на оба канала, FIFO 128 сэмплов

/// Включена ли уже периферия. Инициализацию зовут и тестовые сигналы в
/// `main`, и `CodecSpeaker::new()`; второй проход не нужен — он бы заново
/// поднял аналоговый выход, а это щелчок в динамике.
static INITED: AtomicBool = AtomicBool::new(false);

/// Включить кодек и вывод на наушники. Повторные вызовы ничего не делают.
pub fn init() {
  if INITED.swap(true, Ordering::Relaxed) {
    return;
  }
  unsafe {
    // --- 1. PLL_AUDIO1 ---
    write_volatile(PLL_AUDIO1_CTRL_REG, PLL_AUDIO1_CFG);
    write_volatile(
      PLL_AUDIO1_CTRL_REG,
      PLL_AUDIO1_CFG | PLL_EN | PLL_LOCK_ENABLE,
    );
    for _ in 0..10_000_000u32 {
      if read_volatile(PLL_AUDIO1_CTRL_REG) & PLL_LOCK != 0 {
        break;
      }
      core::hint::spin_loop();
    }

    // --- 2. Модульный такт DAC ---
    write_volatile(AUDIO_CODEC_DAC_CLK_REG, DAC_CLK_CFG);

    // --- 3. Шина и reset ---
    // Сначала пропускаем такт шины, потом снимаем reset: пока такта нет,
    // регистры не защёлкиваются.
    let v = read_volatile(AUDIO_CODEC_BGR_REG);
    write_volatile(AUDIO_CODEC_BGR_REG, v | 1); // AUDIO_CODEC_GATING
    delay(100);
    write_volatile(AUDIO_CODEC_BGR_REG, v | 1 | (1 << 16)); // + RST de-assert
    delay(100);

    // --- 4. Цифровая часть ---
    write_volatile(AC_DAC_FIFOC, FIFOC_CFG | 1); // + FIFO_FLUSH (самосброс)
    // Громкость по обоим каналам 0 дБ, регулятор включён (см. `set_volume`).
    write_volatile(DAC_VOL_CTRL, (1 << 16) | (0xA0 << 8) | 0xA0);
    // EN_DA + HPF_EN: фильтр высоких частот убирает постоянную составляющую,
    // иначе смещение сэмплов APU просто съедает запас по амплитуде.
    write_volatile(AC_DAC_DPC, (1 << 31) | (1 << 18));

    // --- 5. Аналоговая часть ---
    // HPLDO питает HPVCC (пин 97) — по сбросу он выключен, и без него
    // усилитель наушников молчит. ALDO (AVCC, пин 89) включён по умолчанию.
    let v = read_volatile(POWER_REG);
    write_volatile(POWER_REG, v | (1 << 30));
    delay(200_000); // ~0.2 мс на выход LDO

    // Оба ЦАПа. LINEOUT не включаем — он на этой плате никуда не идёт.
    let v = read_volatile(DAC_REG);
    write_volatile(DAC_REG, v | (1 << 15) | (1 << 14));

    // Ramp: RD_EN=1, RMC_EN=0. Без этого выход наушников молчит — проверено
    // на плате (было `fifo drain rate = 24362 Hz`, то есть ЦАП исправно
    // забирал сэмплы, а на HPOUTL/R ничего не появлялось).
    let v = read_volatile(RAMP_REG);
    write_volatile(RAMP_REG, (v & !(1 << 1)) | 1);
    delay(200_000);

    // Усилитель наушников. Остальные биты `HP2_REG` оставляем по сбросу:
    // средняя точка приходит от ramp-ЦАПа (`RSWITCH`=0), петля обратной связи
    // через HPOUTFB не замкнута (`HPFB_*`=0), усиление 0 дБ. Ровно так делает
    // референсный драйвер; попытка «улучшить» это первой версией (VRA1 как
    // опора + буфер HPOUTFB) как раз и дала тишину.
    let v = read_volatile(HP2_REG);
    write_volatile(HP2_REG, v | (1 << 21)); // HP_DRVEN
    delay(200_000);
    write_volatile(HP2_REG, v | (1 << 21) | (1 << 20)); // + HP_DRVOUTEN
    delay(200_000);
  }
}

/// Измерить, с какой частотой кодек реально забирает сэмплы из FIFO.
///
/// Это разделяет две причины тишины. Если функция вернула ~24000 — цифровая
/// часть работает: PLL, модульный такт, FIFO, ЦАП тикают, и виновата
/// аналоговая часть или обвязка на плате. Если вернула 0 — сэмплы вообще не
/// потребляются, и дело в тактировании или в `EN_DA`.
///
/// Метод: залить FIFO доверху нулями и посмотреть по `mcycle`, за сколько
/// освободится половина.
pub fn measure_rate() -> u32 {
  const HALF: u32 = FIFO_DEPTH / 2;
  // Тишина, а не мусор: этот тест слышен как щелчок, если гнать что-то другое.
  while fifo_room() > 0 {
    push(0);
  }
  let t0 = riscv::register::mcycle::read64();
  // Таймаут 100 мс: если DAC не потребляет, ждать бесконечно нельзя.
  let deadline = t0 + crate::utils::CPU_HZ / 10;
  while fifo_room() < HALF {
    if riscv::register::mcycle::read64() > deadline {
      return 0;
    }
    core::hint::spin_loop();
  }
  let dt = riscv::register::mcycle::read64() - t0;
  (HALF as u64 * crate::utils::CPU_HZ / dt) as u32
}

/// Громкость ЦАПа: 0xA0 = 0 дБ, шаг 0.75 дБ, 0x00 = mute.
///
/// Это запас на случай, если через 150К на входе PAM8301 окажется тихо:
/// регулятор цифровой, но идёт до ЦАПа, поэтому подъём не режет разрядность
/// так, как умножение сэмплов у нас в коде.
#[allow(dead_code)]
pub fn set_volume(v: u8) {
  unsafe {
    write_volatile(DAC_VOL_CTRL, (1 << 16) | ((v as u32) << 8) | v as u32);
  }
}

/// Сколько сэмплов ещё влезет в TX FIFO (поле TXE_CNT).
#[inline]
pub fn fifo_room() -> u32 {
  unsafe { (read_volatile(AC_DAC_FIFOS) >> 8) & 0x7FFF }
}

/// Положить сэмпл в FIFO без проверки места. Вызывающий обязан убедиться, что
/// место есть (`fifo_room() > 0`), иначе сэмпл потеряется.
#[inline]
pub fn push(sample: i16) {
  unsafe {
    write_volatile(AC_DAC_TXDATA, sample as u16 as u32);
  }
}

/// Положить сэмпл, дождавшись места. Только для тестовых сигналов без
/// эмулятора: в главном цикле блокироваться нельзя.
pub fn push_blocking(sample: i16) {
  while fifo_room() == 0 {
    core::hint::spin_loop();
  }
  push(sample);
}

/// Регистры для отладочной печати: (DPC, FIFOC, FIFOS, DAC_REG, RAMP, HP1,
/// HP2, POWER, PLL_AUDIO1, DAC_CLK).
pub fn read_regs() -> (u32, u32, u32, u32, u32, u32, u32, u32, u32, u32) {
  unsafe {
    (
      read_volatile(AC_DAC_DPC),
      read_volatile(AC_DAC_FIFOC),
      read_volatile(AC_DAC_FIFOS),
      read_volatile(DAC_REG),
      read_volatile(RAMP_REG),
      read_volatile(HP1_REG),
      read_volatile(HP2_REG),
      read_volatile(POWER_REG),
      read_volatile(PLL_AUDIO1_CTRL_REG),
      read_volatile(AUDIO_CODEC_DAC_CLK_REG),
    )
  }
}
