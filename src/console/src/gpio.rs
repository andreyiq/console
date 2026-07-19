//! GPIO для F133 (Allwinner D1s). Базовый слой для управления пинами.
//!
//! Адреса регистров PIO — из f133_user_manual_v1.0.txt, раздел 9.7.
//! Каждый порт (PB, PC, PD, PE, PF, PG) имеет свой набор регистров:
//!   Pn_CFG0/Pn_CFG1 — конфигурация пинов (4 бита на пин)
//!   Pn_DAT          — данные (чтение/запись уровней)
//!   Pn_DRV0/Pn_DRV1 — сила драйвера
//!   Pn_PULL0        — pull-up/down
//!
//! Здесь только то, что нужно для урока 1: Port C (где SPI0-пины).

use core::ptr::{read_volatile, write_volatile};

pub const PIO_BASE: u32 = 0x0200_0000;

pub const PC_CFG0: *mut u32 = (PIO_BASE + 0x0060) as *mut u32;
pub const PC_DAT: *mut u32 = (PIO_BASE + 0x0070) as *mut u32;
pub const PC_DRV0: *mut u32 = (PIO_BASE + 0x0074) as *mut u32;
pub const PC_PULL0: *mut u32 = (PIO_BASE + 0x0084) as *mut u32;

pub const PE_CFG0: *mut u32 = (PIO_BASE + 0x00C0) as *mut u32;
pub const PE_DAT: *mut u32 = (PIO_BASE + 0x00D0) as *mut u32;
pub const PE_PULL0: *mut u32 = (PIO_BASE + 0x00E4) as *mut u32;

/// Пины порта C. На F133 у Port C 6 ножек (PC0..PC5).
#[repr(u32)]
pub enum PinC {
    P0 = 0, P1, P2, P3, P4, P5,
}

/// Функция пина Port C (4 бита в PC_CFG0, таблица 9.7.5.7).
/// Для нашего проекта хватает Input/Output/Spi0/IoDisable.
#[repr(u32)]
pub enum Func {
    Input     = 0b0000,
    Output    = 0b0001,
    Spi0      = 0b0010,
    IoDisable = 0b1111,
}

/// Pull-up/down для пинов (2 бита на пин в Pn_PULL0).
#[repr(u32)]
pub enum Pull {
    UpDownDisabled = 0b00,
    Up = 0b01,
    Down = 0b10,
}

/// Настраивает пин порта C в функцию `func`.
/// В PC_CFG0 пин `n` занимает биты [4n+3 : 4n].
#[inline]
pub fn pc_set_func(pin: PinC, func: Func) {
    let n = pin as u32;
    let shift = n * 4;
    let mask = 0xF << shift;
    unsafe {
        let old = read_volatile(PC_CFG0);
        write_volatile(PC_CFG0, (old & !mask) | ((func as u32) << shift));
    }
}

/// Поднимает пин порта C в 1 (запись 1 в соответствующий бит PC_DAT).
#[inline]
pub fn pc_set_high(pin: PinC) {
    let bit = 1u32 << (pin as u32);
    unsafe {
        let v = read_volatile(PC_DAT);
        write_volatile(PC_DAT, v | bit);
    }
}

/// Опускает пин порта C в 0 (запись 0 в соответствующий бит PC_DAT).
#[inline]
pub fn pc_set_low(pin: PinC) {
    let bit = 1u32 << (pin as u32);
    unsafe {
        let v = read_volatile(PC_DAT);
        write_volatile(PC_DAT, v & !bit);
    }
}
