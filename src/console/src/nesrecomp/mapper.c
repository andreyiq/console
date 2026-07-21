/* mapper.c — NROM (mapper 0) для mario */
#include "mapper.h"
#include "nes_runtime.h"

static void m_memset(void *dst, int v, unsigned n) {
  volatile uint8_t *d = (volatile uint8_t*)dst;
  for (unsigned i = 0; i < n; i++) d[i] = (uint8_t)v;
}
static void m_memcpy(void *dst, const void *src, unsigned n) {
  volatile uint8_t *d = (volatile uint8_t*)dst;
  const uint8_t *s = (const uint8_t*)src;
  for (unsigned i = 0; i < n; i++) d[i] = s[i];
}
#define memset m_memset
#define memcpy m_memcpy

static const uint8_t *s_prg = 0;
static int s_prg_banks = 0;
static int s_mapper_type = 0;
static int s_mirroring = 0;

void mapper_init(const uint8_t *prg_data, int prg_banks,
                 int mapper_type, int initial_mirroring) {
  s_prg = prg_data;
  s_prg_banks = prg_banks;
  s_mapper_type = mapper_type;
  /* iNES mirroring flag (0=horizontal, 1=vertical) → renderer values
   * (2=vertical, 3=horizontal). NROM передаёт 0 (horizontal) → 3. */
  s_mirroring = (initial_mirroring == 1) ? 2 : 3;
}

void mapper_init_chr(const uint8_t *chr_data, int chr_banks) {
  if (chr_data && chr_banks > 0) {
    memcpy(g_chr_ram, chr_data, 0x2000);
    g_chr_is_rom = 1;
  } else {
    g_chr_is_rom = 0;
  }
}

void mapper_write(uint16_t addr, uint8_t val) {
  /* NROM: нет bank switching, пишем в SRAM если адрес $6000-$7FFF */
  (void)addr; (void)val;
}

const uint8_t *mapper_get_switchable_bank(void) {
  return s_prg ? s_prg : 0;
}

const uint8_t *mapper_get_fixed_bank(void) {
  return s_prg ? s_prg + (s_prg_banks - 1) * 0x4000 : 0;
}

uint8_t mapper_peek_prg(uint16_t addr) {
  if (addr < 0x8000 || !s_prg) return 0;
  return s_prg[addr - 0x8000];
}

int mapper_get_mirroring(void) { return s_mirroring; }
int mapper_get_type(void) { return s_mapper_type; }
int mapper_is_chr_ram(void) { return !g_chr_is_rom; }
void mapper_set_bank(int bank) { g_current_bank = bank; }
