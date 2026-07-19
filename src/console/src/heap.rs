//! Простой bump allocator для `alloc` (Vec/Box) на bare-metal.
//!
//! x-nes использует `Vec<u8>` (Rom: prg/chr) и `Box<T>` (Mapper enum).
//! Без `alloc` крейт не линкуется. У нас 64 MB DDR начиная с 0x40000000,
//! прошивка загружается в начало. Heap — регион в DDR после прошивки.
//!
//! Bump allocator: простой указатель, растёт вверх. Не освобождает память
//! (нет free). Для NES-эмулятора это ОК: ROM и Mapper аллоцируются один раз
//! при старте и живут всё время работы.

use core::alloc::{GlobalAlloc, Layout};
use core::cell::UnsafeCell;
use core::ptr;

extern "C" {
  /// Символ из riscv-rt linker script: начало .heap региона.
  /// _heap_size по умолчанию 0, но мы используем __sheap как старт
  /// bump-аллокатора и сами ограничиваем размер HEAP_SIZE.
  static __sheap: u8;
}

/// Размер heap: 2 MB. ROM Pac-Man: prg=16KB + chr=8KB + mapper overhead.
/// С запасом на Vec/Box.
const HEAP_SIZE: usize = 2 * 1024 * 1024;

struct BumpAllocator {
  next: UnsafeCell<usize>,
}

unsafe impl Sync for BumpAllocator {}

impl BumpAllocator {
  const fn new() -> Self {
    BumpAllocator {
      next: UnsafeCell::new(0),
    }
  }
}

unsafe impl GlobalAlloc for BumpAllocator {
  unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
    let start = &__sheap as *const u8 as usize;
    let heap_end = start + HEAP_SIZE;

    let next = *self.next.get();
    let alloc_start = start + next;
    // Выравниваем alloc_start по layout.align().
    let alloc_start = (alloc_start + layout.align() - 1) & !(layout.align() - 1);
    let alloc_end = alloc_start + layout.size();

    if alloc_end > heap_end {
      // Out of memory.
      return ptr::null_mut();
    }

    let offset = alloc_end - start;
    *self.next.get() = offset;
    alloc_start as *mut u8
  }

  unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
    // Bump allocator: не освобождаем. Память живёт до конца программы.
  }
}

#[global_allocator]
static ALLOCATOR: BumpAllocator = BumpAllocator::new();

/// Печать размера heap при старте (для отладки).
pub fn heap_info() {
  let start = unsafe { &__sheap as *const u8 as usize };
  println!(
    "heap: heap_start=0x{:08x} size={}KB",
    start,
    HEAP_SIZE / 1024
  );
}
