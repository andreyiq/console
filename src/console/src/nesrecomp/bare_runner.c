/* bare_runner.c — минимальный bare-metal runner для nesrecomp (mario, NROM) */
#include "nes_runtime.h"
#ifdef HOST_DEBUG
#include <stdio.h>
#include <execinfo.h>
#endif

static void my_memset(void *dst, int v, unsigned n) {
  volatile uint8_t *d = (volatile uint8_t*)dst;
  for (unsigned i = 0; i < n; i++) d[i] = (uint8_t)v;
}
static void my_memcpy(void *dst, const void *src, unsigned n) {
  volatile uint8_t *d = (volatile uint8_t*)dst;
  const uint8_t *s = (const uint8_t*)src;
  for (unsigned i = 0; i < n; i++) d[i] = s[i];
}
#define memset my_memset
#define memcpy my_memcpy

/* ---- Состояние ---- */
CPU6502State g_cpu;
uint8_t      g_ram[0x0800];
uint8_t      g_sram[0x2000];
uint8_t      g_chr_ram[0x2000];
int          g_chr_is_rom = 0;
uint8_t      g_ppu_oam[0x100];
uint8_t      g_ppu_pal[0x20];
uint8_t      g_ppu_nt[0x1000];

uint8_t g_ppuctrl, g_ppumask, g_ppustatus;
uint8_t g_ppuscroll_x, g_ppuscroll_y;
uint8_t g_oamaddr = 0;
uint16_t g_ppuaddr = 0;
uint16_t g_ppu_t = 0;
uint8_t g_ppu_latch = 0;
uint8_t g_ppu_first_write = 1;
uint8_t g_ppudata_buf = 0;
uint8_t g_fine_x = 0;

int      g_current_bank = 0;
/* g_rti_target определён в mario_full.c */
/* g_recomp_push_all_jsr определён в mario_dispatch.c */
uint64_t g_frame_count = 0;
uint64_t g_last_write_ra = 0;
uint64_t g_nes_cycles = 0;
uint8_t  g_controller1_buttons = 0;
uint8_t  g_controller2_buttons = 0;

/* Общие счётчики nes_write/nes_read для диагностики зависаний */
uint64_t g_nes_write_count = 0;
uint64_t g_nes_read_count = 0;

/* Лимиты trace событий (первые N каждого типа) */
#define TRACE_LIMIT 60
uint64_t g_tr_w2000=0, g_tr_w2001=0, g_tr_w2005=0, g_tr_w2006=0, g_tr_w2007=0;
uint64_t g_tr_r2002=0, g_tr_r2007=0;
uint64_t g_tr_w0770=0, g_tr_w0772=0, g_tr_w0774=0, g_tr_w073C=0;
uint64_t g_tr_dmiss=0;
uint64_t g_tr_w4016=0, g_tr_r4016=0, g_tr_w06FC=0;
uint64_t g_tr_w4014=0, g_tr_w0779=0, g_tr_w0778=0;

/* extern Rust callback — trace событий из C runtime.
 * Теперь APB1 фикс убран и UART работает; trace безопасен. */
void nesrecomp_trace(uint8_t kind, uint16_t addr, uint8_t val, uint64_t frame, uint64_t extra);

/* Controller shift registers (NES protocol: strobe latches buttons,
 * read shifts MSB-first: A,B,Select,Start,Up,Down,Left,Right). */
static uint8_t s_ctrl1_shift = 0;
static uint8_t s_ctrl2_shift = 0;
static int     s_ctrl_strobe = 0;

/* Счётчики записей в PPU regs для отладки */
uint64_t g_w2000 = 0, g_w2001 = 0, g_w2003 = 0, g_w2004 = 0, g_w2005 = 0, g_w2006 = 0, g_w2007 = 0;
uint64_t g_r2002 = 0;
uint64_t g_w4014 = 0;
uint64_t g_w4016 = 0, g_r4016 = 0, g_w4017 = 0;
uint64_t g_w06FC = 0, g_w06FD = 0;
uint64_t g_w0779 = 0, g_w0778 = 0;
uint16_t g_last_2001_val = 0;
uint16_t g_last_2000_val = 0;

/* Отладка $0774 — лог первых 30 записей */
#define DBG_0774_MAX 30
uint64_t g_w0774 = 0;
uint8_t  g_w0774_vals[DBG_0774_MAX];
uint16_t g_w0774_frame[DBG_0774_MAX];
int      g_w0774_count = 0;

DispatchMissPolicy g_dispatch_miss_policy = DISPATCH_MISS_LOG_RETURN;
uint64_t g_dispatch_miss_count = 0;
uint64_t g_inline_dispatch_miss_count = 0;
BrkPolicy g_brk_policy = BRK_DIAG;
uint64_t  g_brk_count = 0;
int g_nested_nmi_policy = 0;
int g_bail_active = 0;
uint16_t g_code_window_base = 0;
int g_render_width = 256;
int g_widescreen_left = 0;
int g_widescreen_right = 0;
int g_ws_eff_left = -1;
int g_ws_eff_right = -1;
int g_ws_oam_sidecar = 0;
int16_t g_oam_x16[64];
int16_t g_ws_shadow_x16[64];
int16_t g_ws_obj_true_rel = 0;
uint8_t g_ws_obj_rel8 = 0;
uint8_t g_ws_obj_ctx_valid = 0;
int g_zapper_enabled = 0;
int g_zapper_x = 0, g_zapper_y = 0, g_zapper_trigger = 0;
int g_spr0_split_active = 0;
int g_spr0_reads_ctr_legacy = 0;
int g_spr0_predict_disable = 1;
int g_predicted_spr0_scanline = 240;
int g_spr0_split_write_scanline = -1;
uint8_t g_ppuscroll_x_hud = 0;
uint8_t g_ppuscroll_y_hud = 0;
uint8_t g_ppuctrl_hud = 0;
int g_mmc3_r6_odd = 0;
int g_mmc3_r7_even = 0;
int g_mmc3_bank_a000 = 0;
int g_mmc3_win_bank8k[4] = {0,0,0,0};
char g_exe_dir[260] = ".";
uint32_t g_miss_count_any = 0;
uint16_t g_miss_last_addr = 0;
uint64_t g_miss_last_frame = 0;
uint16_t g_miss_unique_addrs[MAX_MISS_UNIQUE] = {0};
int      g_miss_unique_count = 0;
MissRecord g_miss_ring[MAX_MISS_RING];
int        g_miss_ring_head = 0;
int        g_miss_ring_count = 0;

/* ---- PRG ROM (2 banks по 16KB для mario NROM) ---- */
extern const uint8_t g_mario_prg[]; /* 32KB */
extern const uint8_t g_mario_chr[]; /* 8KB */
extern const int g_mario_prg_size;
extern const int g_mario_chr_size;

/* ---- Memory map (NROM) ---- */
uint8_t nes_read(uint16_t addr) {
  g_nes_read_count++;
  if (addr < 0x2000) {
    uint8_t v = g_ram[addr & 0x07FF];
    /* Trace ключевых game variables для отладки зависания */
    if (addr == 0x0770 || addr == 0x0772 || addr == 0x0776) {
      nesrecomp_trace(20, addr, v, g_frame_count, 0);
    }
    return v;
  } else if (addr < 0x4000) {
    uint16_t reg = addr & 7;
    uint8_t v = ppu_read_reg(reg);
    if (reg == 2 && g_tr_r2002 < TRACE_LIMIT) {
      g_tr_r2002++;
      /* extra = g_ppustatus до read (для понимания что было) */
      nesrecomp_trace(5, addr, v, g_frame_count, g_r2002);
    }
    if (reg == 7 && g_tr_r2007 < TRACE_LIMIT) {
      g_tr_r2007++;
      uint16_t after = (uint16_t)(g_ppuaddr + ((g_ppuctrl & 4) ? 32 : 1)) & 0x3FFF;
      nesrecomp_trace(6, g_ppuaddr, v, g_frame_count, after);
    }
    return v;
  } else if (addr < 0x4020) {
    /* APU/IO */
    if (addr == 0x4016) {
      /* Controller 1: NES protocol — strobe ON возвращает MSB (A button),
       * strobe OFF возвращает 1 бит за read (MSB-first), shift заполняется 1.
       * bit 0 = button bit, bits 6-7 = open bus (0x40). */
      uint8_t v;
      if (s_ctrl_strobe) v = 0x40 | (g_controller1_buttons >> 7);
      else {
        uint8_t bit = (s_ctrl1_shift & 0x80) ? 1 : 0;
        s_ctrl1_shift = (uint8_t)((s_ctrl1_shift << 1) | 1);
        v = 0x40 | bit;
      }
      if (g_tr_r4016 < TRACE_LIMIT) {
        g_tr_r4016++;
        nesrecomp_trace(16, addr, v, g_frame_count, s_ctrl_strobe);
      }
      return v;
    }
    if (addr == 0x4017) {
      if (s_ctrl_strobe) return 0x40 | (g_controller2_buttons >> 7);
      uint8_t bit = (s_ctrl2_shift & 0x80) ? 1 : 0;
      s_ctrl2_shift = (uint8_t)((s_ctrl2_shift << 1) | 1);
      return 0x40 | bit;
    }
    return 0x40; /* open bus */
  } else if (addr < 0x8000) {
    return g_sram[addr - 0x6000];
  } else {
    /* PRG ROM: $8000-$FFFF. NROM 32KB → addr - 0x8000 */
    return g_mario_prg[addr - 0x8000];
  }
}

void nes_write(uint16_t addr, uint8_t val) {
  uintptr_t ra_val;
  #if defined(__riscv) && __riscv_xlen == 64
    __asm__ __volatile__("mv %0, x1" : "=r"(ra_val) : :);
  #else
    ra_val = 0;  /* host: не используем ra для отладки */
  #endif
  g_last_write_ra = (uint64_t)ra_val;
  g_nes_write_count++;
  if (addr < 0x2000) {
    g_ram[addr & 0x07FF] = val;
    uint16_t a = addr & 0x07FF;
    /* Отладка $0774 — счётчик + лог первых 30 */
    if (a == 0x0774) {
      g_w0774++;
      if (g_w0774_count < DBG_0774_MAX) {
        g_w0774_vals[g_w0774_count] = val;
        g_w0774_frame[g_w0774_count] = (uint16_t)g_frame_count;
        g_w0774_count++;
      }
      if (g_tr_w0774 < TRACE_LIMIT) {
        g_tr_w0774++;
        nesrecomp_trace(9, addr, val, g_frame_count, g_last_write_ra);
      }
    }
    /* Отладка $0772 — trace первых N */
    if (a == 0x0772 && g_tr_w0772 < TRACE_LIMIT) {
      g_tr_w0772++;
      nesrecomp_trace(8, addr, val, g_frame_count, g_ram[0x0774]);
    }
    /* Отладка $0770 — trace первых N */
    if (a == 0x0770 && g_tr_w0770 < TRACE_LIMIT) {
      g_tr_w0770++;
      nesrecomp_trace(7, addr, val, g_frame_count, g_ram[0x0772]);
    }
    /* Отладка $073C — trace первых N */
    if (a == 0x073C && g_tr_w073C < TRACE_LIMIT) {
      g_tr_w073C++;
      nesrecomp_trace(10, addr, val, g_frame_count, g_ram[0x0774]);
    }
    /* Отладка $06FC (controller state) — trace первых N */
    if (a == 0x06FC && g_tr_w06FC < TRACE_LIMIT) {
      g_tr_w06FC++;
      nesrecomp_trace(14, addr, val, g_frame_count, g_ram[0x06FD]);
    }
    /* Отладка $0779 (PPUMASK copy) — trace первых N */
    if (a == 0x0779 && g_tr_w0779 < TRACE_LIMIT) {
      g_tr_w0779++;
      nesrecomp_trace(17, addr, val, g_frame_count, g_ram[0x0774]);
    }
    /* Отладка $0778 (PPUCTRL copy) — trace первых N */
    if (a == 0x0778 && g_tr_w0778 < TRACE_LIMIT) {
      g_tr_w0778++;
      nesrecomp_trace(18, addr, val, g_frame_count, g_ram[0x0774]);
    }
    /* Trace nes_write(0x0004) — первая инструкция case 0 в func_8212_b0 */
    if (a == 0x0004) {
      nesrecomp_trace(21, addr, val, g_frame_count, 0);
    }
  } else if (addr < 0x4000) {
    uint16_t reg = addr & 7;
    /* Отладка записей в PPU regs */
    switch (reg) {
      case 0: g_w2000++; g_last_2000_val = val;
        if (g_tr_w2000 < TRACE_LIMIT) {
          g_tr_w2000++;
          nesrecomp_trace(0, addr, val, g_frame_count, 0);
        } break;
      case 1: g_w2001++; g_last_2001_val = val;
        if (g_tr_w2001 < 20) {
          g_tr_w2001++;
          nesrecomp_trace(1, addr, val, g_frame_count, g_last_write_ra);
        } break;
      case 3: g_w2003++; break;
      case 4: g_w2004++; break;
      case 5: g_w2005++;
        if (g_tr_w2005 < TRACE_LIMIT) {
          g_tr_w2005++;
          /* extra = 0 если first write (scroll_x), 1 если second (scroll_y) */
          nesrecomp_trace(2, addr, val, g_frame_count, g_ppu_first_write ? 0 : 1);
        } break;
      case 6: g_w2006++;
        if (g_tr_w2006 < TRACE_LIMIT) {
          g_tr_w2006++;
          /* extra = 0 если first write (hi), 1 если second (lo); addr = new ppuaddr */
          nesrecomp_trace(3, addr, val, g_frame_count, g_ppu_first_write ? 0 : 1);
        } break;
      case 7: g_w2007++;
        if (g_tr_w2007 < TRACE_LIMIT) {
          g_tr_w2007++;
          /* extra = ppuaddr AFTER write (для понимания последовательности) */
          uint16_t after = (uint16_t)(g_ppuaddr + ((g_ppuctrl & 4) ? 32 : 1)) & 0x3FFF;
          nesrecomp_trace(4, g_ppuaddr, val, g_frame_count, after);
        } break;
    }
    ppu_write_reg(reg, val);
  } else if (addr == 0x4014) {
    g_w4014++;
    if (g_tr_w4014 < TRACE_LIMIT) {
      g_tr_w4014++;
      nesrecomp_trace(19, addr, val, g_frame_count, g_w4014);
    }
    /* OAM DMA: copy 256 bytes from val<<8 to g_ppu_oam */
    uint16_t src = ((uint16_t)val) << 8;
    for (int i = 0; i < 256; i++) g_ppu_oam[i] = nes_read(src + i);
  } else if (addr == 0x4016) {
    /* Controller strobe: bit 0 = 1 → strobe ON, falling edge → latch buttons */
    if (g_tr_w4016 < TRACE_LIMIT) {
      g_tr_w4016++;
      nesrecomp_trace(15, addr, val, g_frame_count, g_controller1_buttons);
    }
    if (val & 1) {
      s_ctrl_strobe = 1;
    } else if (s_ctrl_strobe) {
      s_ctrl_strobe = 0;
      s_ctrl1_shift = g_controller1_buttons;
      s_ctrl2_shift = g_controller2_buttons;
    }
  } else if (addr < 0x4020) {
    /* APU/IO — stub */
  } else if (addr < 0x8000) {
    g_sram[addr - 0x6000] = val;
  } else {
    /* PRG ROM — read-only на NROM, но mapper_write может ловить */
    mapper_write(addr, val);
  }
}

uint16_t nes_read16(uint16_t addr) {
  return nes_read(addr) | ((uint16_t)nes_read(addr + 1) << 8);
}

uint16_t nes_read16zp(uint8_t zp_addr) {
  uint8_t lo = nes_read(zp_addr);
  uint8_t hi = nes_read((uint8_t)(zp_addr + 1));
  return lo | ((uint16_t)hi << 8);
}

uint16_t nes_read16_jmpbug(uint16_t addr) {
  uint8_t lo = nes_read(addr);
  uint8_t hi = nes_read((addr & 0xFF00) | ((addr + 1) & 0xFF));
  return lo | ((uint16_t)hi << 8);
}

uint8_t game_ram_read_hook(uint16_t pc, uint16_t addr, uint8_t val) {
  return val; /* no-op */
}

/* ---- Dispatch ---- */
/* call_by_address определён в mario_dispatch.c */
int call_by_address_tail(uint16_t addr, int caller_bank) {
  return nes_dispatch_call(addr, caller_bank);
}

int nes_dispatch_call(uint16_t addr, int caller_bank) {
  /* Сгенерированный call_by_address_cb обрабатывает switch.
   * Если miss — fallback на interp. */
  int rc = call_by_address_cb(addr, caller_bank);
  return rc;
}

void nes_log_dispatch_miss(uint16_t addr) {
  g_dispatch_miss_count++;
  g_miss_count_any++;
  g_miss_last_addr = addr;
  g_miss_last_frame = g_frame_count;
  if (g_tr_dmiss < TRACE_LIMIT) {
    g_tr_dmiss++;
    nesrecomp_trace(13, addr, 0, g_frame_count, g_dispatch_miss_count);
  }
}

void nes_log_dispatch_miss_bank(uint16_t gen_addr, uint16_t cpu_addr, int bank) {
  nes_log_dispatch_miss(cpu_addr);
}

void nes_log_inline_miss(uint16_t dispatch_pc, uint8_t a_val) {
  g_inline_dispatch_miss_count++;
}

void nes_record_dispatch_miss(uint16_t addr) { nes_log_dispatch_miss(addr); }
void nes_record_dispatch_miss_bank(uint16_t gen_addr, uint16_t cpu_addr, int bank) {
  nes_log_dispatch_miss(cpu_addr);
}
void nes_dispatch_miss_apply_policy(uint16_t addr) { (void)addr; }

void nes_set_dispatch_miss_policy(DispatchMissPolicy p) { g_dispatch_miss_policy = p; }
void nes_set_brk_policy(BrkPolicy p) { g_brk_policy = p; }

void nes_brk_executed(uint16_t pc) {
  g_brk_count++;
  /* DIAG: log + return (как RTS) */
}

void nes_dump_dispatch_ring(void) {}
void nes_dring_mark(char kind, uint16_t tag) { (void)kind; (void)tag; }

/* ---- VBlank ---- */
static int s_vblank_pending = 0;
static int s_vblank_depth = 0;
static int s_vblank_firing = 1;
static uint32_t s_ops_count = 0;
static uint32_t s_frame_budget = 29780;

void maybe_trigger_vblank(int cycles) {
  uint32_t c = (cycles > 0) ? (uint32_t)cycles : 1;
  g_nes_cycles += c;
  s_ops_count += c;

  if (s_ops_count < s_frame_budget) return;
  if (s_vblank_depth > 0) return; /* defer nested NMI */

  /* Frame budget exhausted — fire VBlank */
  s_ops_count -= s_frame_budget;
  s_vblank_depth = 1;
  g_ppustatus = (g_ppustatus & ~0x40) | 0x80; /* set VBlank, clear sprite0 hit */

  /* Fire NMI only if enabled (PPUCTRL bit 7) */
  if (g_ppuctrl & 0x80) {
    nes_vblank_callback();
  }

  s_vblank_depth = 0;
}

void maybe_fire_pending_vblank(void) {
  /* не используется — maybe_trigger_vblank вызывает nes_vblank_callback напрямую */
  (void)s_vblank_pending;
}

void runtime_set_vblank_firing(int active) { s_vblank_firing = active; }
int  runtime_get_vblank_depth(void) { return s_vblank_depth; }
void runtime_reset_vblank_depth(void) { s_vblank_depth = 0; }
void runtime_begin_post_nmi(void) { s_vblank_depth = 0; }
void runtime_end_post_nmi(void) {}

uint32_t runtime_pop_nmi_fires(void) { return 0; }
uint32_t runtime_pop_cycle_budget_used(void) { return 0; }
uint32_t runtime_pop_instrs_ticked(void) { return 0; }
uint32_t runtime_pop_forced_caps(void) { return 0; }

void nes_fring_push(char kind, uint16_t aux) { (void)kind; (void)aux; }
uint16_t nes_fring_shadow_digest(void) { return 0; }
int nes_fring_last(int n, NesFrameEvt *dst) { (void)n; (void)dst; return 0; }
void nes_fring_set_dma_page(uint8_t page) { (void)page; }
void nes_fring_init_dump(void) {}

/* ---- Runtime init ---- */
void runtime_init(void) {
  memset(g_ram, 0, sizeof(g_ram));
  memset(g_sram, 0xFF, sizeof(g_sram)); /* fresh battery SRAM = all 0xFF */
  memset(g_ppu_oam, 0, sizeof(g_ppu_oam));
  memset(g_ppu_pal, 0, sizeof(g_ppu_pal));
  memset(g_ppu_nt, 0, sizeof(g_ppu_nt));
  memset(&g_cpu, 0, sizeof(g_cpu));
  g_cpu.S = 0xFD; /* стандартное значение после RESET на 6502 */
  g_cpu.I = 1;
  /* Явно обнуляем все PPU variables — BSS init может не сработать на железе. */
  g_ppuctrl = 0;
  g_ppumask = 0;
  g_ppustatus = 0;
  g_ppuscroll_x = 0;
  g_ppuscroll_y = 0;
  g_oamaddr = 0;
  g_ppuaddr = 0;
  g_ppu_t = 0;
  g_ppu_latch = 0;
  g_ppu_first_write = 1;
  g_ppudata_buf = 0;
  g_fine_x = 0;
  g_nes_cycles = 0;
  g_frame_count = 0;
  s_ctrl_strobe = 0;
  s_ctrl1_shift = 0;
  s_ctrl2_shift = 0;
  g_controller1_buttons = 0;
  g_controller2_buttons = 0;
  /* iNES mirroring: 0=horizontal, 1=vertical. Renderer: 2=vertical, 3=horizontal.
   * Mario (NROM) — horizontal mirroring, передаём 0 → конвертируем в 3. */
  mapper_init(g_mario_prg, 2, 0, 0);
  /* CHR ROM → g_chr_ram (копируем, т.к. mario имеет CHR ROM) */
  memcpy(g_chr_ram, g_mario_chr, 0x2000);
  g_chr_is_rom = 1;
}

uint8_t *runner_get_prg_bank_rw(int bank_num) {
  if (bank_num < 0 || bank_num >= 2) return 0;
  return (uint8_t*)&g_mario_prg[bank_num * 0x4000];
}

void runtime_get_vblank_state(uint32_t *ops_count, int *vblank_depth) {
  *ops_count = 0; *vblank_depth = s_vblank_depth;
}
void runtime_set_vblank_state(uint32_t ops_count, int vblank_depth) {
  (void)ops_count; s_vblank_depth = vblank_depth;
}
void runtime_get_controller_shift(uint8_t *s1, uint8_t *s2, uint8_t *strobe) {
  *s1 = s_ctrl1_shift; *s2 = s_ctrl2_shift; *strobe = (uint8_t)s_ctrl_strobe;
}
void runtime_set_controller_shift(uint8_t s1, uint8_t s2, uint8_t strobe) {
  s_ctrl1_shift = s1; s_ctrl2_shift = s2; s_ctrl_strobe = strobe;
}
void runtime_sync_scroll_from_t(void) {}
void runtime_sync_scroll_from_v(void) {}
uint8_t runtime_get_ppudata_buf(void) { return g_ppudata_buf; }
void runtime_set_ppudata_buf(uint8_t val) { g_ppudata_buf = val; }
uint16_t runtime_get_ppuaddr(void) { return g_ppuaddr; }
void runtime_set_ppuaddr(uint16_t addr) { g_ppuaddr = addr; }
uint16_t runtime_get_ppu_t(void) { return g_ppu_t; }
int runtime_scroll_from_t_valid(void) { return 0; }
void runtime_get_latch_state(uint8_t *al, uint8_t *sl) { *al = 0; *sl = 0; }
void runtime_set_latch_state(uint8_t al, uint8_t sl) { (void)al; (void)sl; }

/* ---- PPU state для отладки (вызывается из Rust) ---- */
uint64_t nesrecomp_get_ppu_state(void) {
  return ((uint64_t)g_ppumask)        |
         ((uint64_t)g_ppuctrl)    << 8  |
         ((uint64_t)g_ppustatus)  << 16 |
         ((uint64_t)g_ppuscroll_x) << 24 |
         ((uint64_t)g_ppuscroll_y) << 32;
}

/* Полный счётчик PPU операций (вызывается из Rust) */
uint64_t nesrecomp_get_ppu_counters(uint64_t *w2000, uint64_t *w2001, uint64_t *w2005,
                                      uint64_t *w2006, uint64_t *w2007, uint64_t *r2002,
                                      uint64_t *w4014, uint16_t *last2001) {
  *w2000 = g_w2000; *w2001 = g_w2001; *w2005 = g_w2005;
  *w2006 = g_w2006; *w2007 = g_w2007; *r2002 = g_r2002;
  *w4014 = g_w4014; *last2001 = g_last_2001_val;
  return g_w2001;
}

/* Значения RAM переменных для отладки */
uint8_t nesrecomp_get_ram(uint16_t addr) { return g_ram[addr & 0x07FF]; }

/* Dispatch miss counters для отладки */
uint64_t nesrecomp_get_dispatch_miss(void) { return g_dispatch_miss_count; }
uint64_t nesrecomp_get_inline_miss(void) { return g_inline_dispatch_miss_count; }
uint8_t nesrecomp_get_ctrl1(void) { return g_controller1_buttons; }

/* Полное состояние для отладки — все в одном вызове */
void nesrecomp_get_debug_state(
    uint8_t *A, uint8_t *X, uint8_t *Y, uint8_t *S,
    uint8_t *ppuctrl, uint8_t *ppumask, uint8_t *ppustatus,
    uint8_t *scroll_x, uint8_t *scroll_y,
    uint16_t *ppuaddr, uint16_t *ppu_t,
    uint8_t *ppudata_buf, uint8_t *ppu_first_write,
    uint64_t *w0774, uint64_t *w2000, uint16_t *last2000,
    uint64_t *frame_count, uint64_t *nes_cycles) {
  *A = g_cpu.A; *X = g_cpu.X; *Y = g_cpu.Y; *S = g_cpu.S;
  *ppuctrl = g_ppuctrl; *ppumask = g_ppumask; *ppustatus = g_ppustatus;
  *scroll_x = g_ppuscroll_x; *scroll_y = g_ppuscroll_y;
  *ppuaddr = g_ppuaddr; *ppu_t = g_ppu_t;
  *ppudata_buf = g_ppudata_buf; *ppu_first_write = g_ppu_first_write;
  *w0774 = g_w0774; *w2000 = g_w2000; *last2000 = g_last_2000_val;
  *frame_count = g_frame_count; *nes_cycles = g_nes_cycles;
}

/* Общие счётчики I/O для диагностики зависаний */
void nesrecomp_get_io_counters(uint64_t *wr, uint64_t *rd) {
  *wr = g_nes_write_count;
  *rd = g_nes_read_count;
}

/* Счётчики вызовов ключевых функций */
uint64_t g_call_8212 = 0;  /* OperModeExecutionTree */
uint64_t g_call_8231 = 0;   /* TitleScreenMode */
uint64_t g_call_8FCF = 0;   /* InitializeGame */
uint64_t g_call_8FCF_done = 0;
uint64_t g_call_8567 = 0;   /* ScreenRoutines */
uint64_t g_call_858B = 0;   /* InitScreen */
uint64_t g_call_86A8 = 0;   /* DisplayIntermediate */
uint64_t g_call_86E6 = 0;   /* AreaParserTaskControl */
uint64_t g_call_NMI = 0;    /* NMI handler */
uint64_t g_call_RESET = 0;  /* RESET */

void nesrecomp_get_call_counters(uint64_t *c8212, uint64_t *c8231, uint64_t *c8FCF,
    uint64_t *c8FCF_done, uint64_t *c8567, uint64_t *c858B,
    uint64_t *c86A8, uint64_t *c86E6, uint64_t *cNMI, uint64_t *cRESET) {
  *c8212 = g_call_8212; *c8231 = g_call_8231; *c8FCF = g_call_8FCF;
  *c8FCF_done = g_call_8FCF_done; *c8567 = g_call_8567; *c858B = g_call_858B;
  *c86A8 = g_call_86A8; *c86E6 = g_call_86E6; *cNMI = g_call_NMI; *cRESET = g_call_RESET;
}

/* Вывести лог записей в $0774 */
__attribute__((noinline)) void nesrecomp_dump_0774(void) {
#ifdef HOST_DEBUG
  printf("w0774=%llu (logged %d):\n", (unsigned long long)g_w0774, g_w0774_count);
  for (int i = 0; i < g_w0774_count; i++) {
    printf("  [%d] frame=%u val=%#04x\n", i, g_w0774_frame[i], g_w0774_vals[i]);
  }
  fflush(stdout);
#endif
}

void debug_server_request_pause(const char *reason) { (void)reason; }

void runtime_set_zapper_framebuf(const uint32_t *fb) { (void)fb; }
void runtime_set_zapper_render_callback(zapper_render_fn fn) { (void)fn; }
void runtime_set_zapper_snapshot(const uint32_t *fb) { (void)fb; }

void runner_screenshot(const char *path) { (void)path; }

/* ---- NES framebuffer: ДВА буфера 256x240 RGB888 (184KB каждый) ----
 * Двойная буферизация для pipeline: пока DMA передаёт кадр N из buffer A,
 * ppu_render_frame пишет кадр N+1 в buffer B. Общее время кадра =
 * max(ppu, fl) вместо ppu + fl.
 */
static uint8_t g_nes_framebuf_a[256 * 240 * 3];
static uint8_t g_nes_framebuf_b[256 * 240 * 3];
static int     g_cur_buf = 0;  /* 0 = A, 1 = B */

/* ---- VBlank callback — вызывается из maybe_trigger_vblank ----
 * Замеры mcycle для профилирования (читаются из Rust через getters):
 *   g_t0      — вход в nes_vblank_callback (старт NMI)
 *   g_t1      — после func_NMI()          (CPU эмуляция внутри NMI)
 *   g_t2      — после ppu_render_frame()  (PPU рендеринг)
 *   g_prev_t0 — старт прошлого NMI (для between_nmi = t0 - prev_t0)
 */
static uint64_t g_t0 = 0;
static uint64_t g_t1 = 0;
static uint64_t g_t2 = 0;
static uint64_t g_prev_t0 = 0;

static inline uint64_t read_mcycle(void) {
  uint64_t v;
  __asm__ __volatile__("rdcycle %0" : "=r"(v));
  return v;
}

void nes_vblank_callback(void) {
  g_t0 = read_mcycle();
  g_ppustatus |= 0x80; /* VBlank flag */
  func_NMI();
  g_t1 = read_mcycle();
  g_frame_count++;
  /* Рендерим кадр в ТЕКУЩИЙ буфер (A или B) */
  uint8_t *buf = (g_cur_buf == 0) ? g_nes_framebuf_a : g_nes_framebuf_b;
  ppu_render_frame((uint32_t*)buf);
  g_t2 = read_mcycle();
  /* Сообщаем Rust: кадр готов, flush на дисплей (pipeline: Rust сам
   * дождётся завершения прошлого DMA, потом запустит новый). */
  extern void nesrecomp_on_frame(uint64_t frame, const uint8_t *buf, int w, int h);
  nesrecomp_on_frame(g_frame_count, buf, 256, 240);
  /* Меняем буфер для следующего кадра */
  g_cur_buf ^= 1;
  g_prev_t0 = g_t0;
}

uint64_t nesrecomp_get_t0(void)      { return g_t0; }
uint64_t nesrecomp_get_t1(void)      { return g_t1; }
uint64_t nesrecomp_get_t2(void)      { return g_t2; }
uint64_t nesrecomp_get_prev_t0(void) { return g_prev_t0; }
