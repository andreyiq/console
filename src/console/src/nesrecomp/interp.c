/* interp.c — минимальный 6502 interpreter для dispatch miss.
 * Для mario (полностью recompiled) miss'ы не должны происходить.
 * Если произойдёт — halt с диагностикой. */
#include "nes_runtime.h"
#include <stdint.h>

/* Полный 6502 interpreter здесь не реализован — для mario не нужен.
 * Если dispatch miss произойдёт, runtime вызовет nes_interp_dispatch.
 * Возвращаем 0 (miss policy применится). */

int nes_interp_dispatch(uint16_t addr) {
  (void)addr;
  return 0;
}

int nes_interp_dispatch_bank(uint16_t cpu_addr, uint16_t gen_addr, int bank) {
  (void)cpu_addr; (void)gen_addr; (void)bank;
  return 0;
}
