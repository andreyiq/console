/* ppu.c — PPU registers + scanline-based renderer для NROM */
#include "nes_runtime.h"
#include "mapper.h"

extern uint16_t g_ppu_t;
extern uint8_t  g_ppu_first_write;
extern uint8_t  g_fine_x;
extern uint8_t  g_ppudata_buf;
extern uint16_t g_ppuaddr;

void ppu_write_vram(uint16_t addr, uint8_t val);
uint8_t ppu_read_vram(uint16_t addr);

/* ---- Mirroring ---- */
static uint16_t nt_mirror_addr(uint16_t addr) {
  /* addr in $2000-$2FFF */
  uint16_t off = addr & 0x0FFF;
  int nt = (off >> 10) & 3;
  int m = mapper_get_mirroring();
  int target;
  if (m == 2) { /* vertical: A=B, C=D */
    target = nt & 1;
  } else if (m == 3) { /* horizontal: A=C, B=D */
    target = (nt >> 1) & 1;
  } else { /* one-screen */
    target = (m == 0) ? 0 : 1;
  }
  return (uint16_t)(0x2000 | (target << 10) | (off & 0x03FF));
}

/* ---- PPU register access ---- */
void ppu_write_reg(uint16_t reg, uint8_t val) {
  switch (reg) {
    case 0: /* $2000 PPUCTRL */
      g_ppuctrl = val;
      g_ppu_t = (g_ppu_t & 0xF3FF) | (((uint16_t)(val & 3)) << 10);
      break;
    case 1: /* $2001 PPUMASK */
      g_ppumask = val;
      break;
    case 2: /* $2002 PPUSTATUS — read-only */
      break;
    case 3: /* $2003 OAMADDR */
      g_oamaddr = val;
      break;
    case 4: /* $2004 OAMDATA */
      g_ppu_oam[g_oamaddr] = val;
      g_oamaddr = (uint8_t)(g_oamaddr + 1);
      break;
    case 5: /* $2005 PPUSCROLL */
      if (g_ppu_first_write) {
        g_ppuscroll_x = val;
        g_fine_x = val & 7;
        g_ppu_t = (g_ppu_t & 0xFFE0) | (val >> 3);
        g_ppu_first_write = 0;
      } else {
        g_ppuscroll_y = val;
        /* second write: fine Y → t[12:14], coarse Y → t[5:9].
         * Mask 0x0C1F сохраняет nametable (bits 10-11) и coarse X (bits 0-4). */
        g_ppu_t = (g_ppu_t & 0x0C1F) | (((uint16_t)(val & 7)) << 12)
                                   | (((uint16_t)(val >> 3)) << 5);
        g_ppu_first_write = 1;
      }
      break;
    case 6: /* $2006 PPUADDR */
      if (g_ppu_first_write) {
        /* first write: val[5:0] → t[13:8], clear t bit 14 */
        g_ppu_t = (g_ppu_t & 0x00FF) | (((uint16_t)(val & 0x3F)) << 8);
        g_ppuaddr = (uint16_t)val << 8; /* legacy sync */
        g_ppu_first_write = 0;
      } else {
        /* second write: val → t[7:0], then v = t */
        g_ppu_t = (g_ppu_t & 0xFF00) | val;
        g_ppuaddr = g_ppu_t & 0x3FFF; /* v = t (14-bit VRAM address) */
        g_ppu_first_write = 1;
      }
      g_ppudata_buf = 0; /* $2006 write сбрасывает read buffer */
      break;
    case 7: /* $2007 PPUDATA */
      ppu_write_vram(g_ppuaddr, val);
      g_ppuaddr += (g_ppuctrl & 4) ? 32 : 1;
      g_ppuaddr &= 0x3FFF;
      break;
  }
}

void ppu_write_vram(uint16_t addr, uint8_t val) {
  addr &= 0x3FFF;
  if (addr < 0x2000) {
    if (!g_chr_is_rom) g_chr_ram[addr] = val;
  } else if (addr < 0x3F00) {
    g_ppu_nt[nt_mirror_addr(addr) & 0x0FFF] = val;
  } else if (addr < 0x4000) {
    /* NES palette mirror: $3F10/$3F14/$3F18/$3F1C share storage
     * with $3F00/$3F04/$3F08/$3F0C (transparent color slots). */
    uint16_t idx = addr & 0x1F;
    if (idx == 0x10 || idx == 0x14 || idx == 0x18 || idx == 0x1C)
      idx &= 0x0F;
    g_ppu_pal[idx] = val;
  }
}

uint8_t ppu_read_reg(uint16_t reg) {
  switch (reg) {
    case 0: return g_ppuctrl;
    case 1: return g_ppumask;
    case 2: { /* PPUSTATUS */
      extern uint64_t g_r2002;
      g_r2002++;
      /* Sprite-0 hit pulse: если bit 6 set — очищаем (consume).
       * Иначе — pulse после 3 reads (fallback для games spin-wait на sprite-0 hit,
       * т.к. у нас нет dot-accurate PPU). Без этого game зависает в spin-wait. */
      if (g_ppustatus & 0x40) {
        g_ppustatus &= ~0x40;
        g_spr0_reads_ctr_legacy = 0;
        g_spr0_split_active = 1;
      } else if (++g_spr0_reads_ctr_legacy >= 3) {
        g_ppuscroll_x_hud = g_ppuscroll_x;
        g_ppuscroll_y_hud = g_ppuscroll_y;
        g_ppuctrl_hud     = g_ppuctrl & 0x38;
        g_ppustatus |= 0x40;
        g_spr0_reads_ctr_legacy = 0;
        g_spr0_split_active = 1;
      }
      /* bits 7-5 = ppustatus, bits 4-0 = open bus (нет I/O latch — возвращаем 0). */
      uint8_t v = (uint8_t)(g_ppustatus & 0xE0);
      g_ppustatus &= ~0x80; /* clear VBlank flag on read (standard NES) */
      g_ppu_first_write = 1; /* $2002 read сбрасывает latch для $2005/$2006 */
      return v;
    }
    case 3: return g_oamaddr;
    case 4: return g_ppu_oam[g_oamaddr];
    case 5: return g_ppuscroll_x;
    case 6: return g_ppuaddr >> 8;
    case 7: {
      /* NES $2007 read: buffered для CHR/NT, immediate для palette.
       * Возвращаем OLD buffer, затем обновляем buffer новым значением. */
      uint16_t a = g_ppuaddr & 0x3FFF;
      uint8_t v;
      if (a >= 0x3F00) {
        /* palette: immediate read */
        v = g_ppu_pal[a & 0x1F] & 0x3F;
        /* also update buffer с NT mirror (как на реальном NES) */
        g_ppudata_buf = ppu_read_vram(a & 0x0FFF);
      } else {
        v = g_ppudata_buf;
        g_ppudata_buf = ppu_read_vram(a);
      }
      g_ppuaddr += (g_ppuctrl & 4) ? 32 : 1;
      g_ppuaddr &= 0x3FFF;
      return v;
    }
  }
  return 0;
}

uint8_t ppu_read_vram(uint16_t addr) {
  if (addr < 0x2000) return g_chr_ram[addr];
  if (addr < 0x3F00) return g_ppu_nt[nt_mirror_addr(addr) & 0x0FFF];
  if (addr < 0x4000) return g_ppu_pal[addr & 0x1F] & 0x3F;
  return 0;
}

/* ---- Palette → RGB (NTSC) ---- */
static const uint32_t s_pal_rgb[64] = {
  0x666666,0x002A88,0x1412A7,0x3B00A4,0x5C007E,0x6E0040,0x6C0700,0x521D00,
  0x393900,0x2A4B00,0x004D00,0x004700,0x004247,0x000000,0x000000,0x000000,
  0xB4B4B4,0x0073EF,0x4A4AFF,0x8B30FF,0xC600FF,0xFF44FF,0xFF7788,0xFF9933,
  0xFFB333,0xFFD733,0xFFFF33,0xCCE333,0x88EE33,0x33EE33,0x33EE88,0x33EEEE,
  0xFFFFFF,0x66B4FF,0x99CCFF,0xCC99FF,0xFF99FF,0xFFBBFF,0xFFCCCC,0xFFDDBB,
  0xFFEEBB,0xFFFFBB,0xDDEEBB,0xAADD99,0x77DD99,0x55DDAA,0x33DDCC,0x33CCCC,
  0x000000,0x000000,0x000000,0x000000,0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,
  0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,0xFFFFFF,
};

/* ---- Scanline renderer with tile caching ----
 * Рисует background + sprites в framebuffer 256x240 RGB888 (3 байта/пиксель).
 * Совместимо с нашим display.flush_region_dma (OFFSET_X=112, OFFSET_Y=40).
 *
 * Оптимизации:
 *  1. Tile caching: для каждой scanline prefetch 32 tiles (tile_idx + attr + 8 pattern bytes
 *     для fine_y). Вместо 5 reads/пиксель → 1 read из cache. Выигрыш ~40x.
 *  2. Sprite list per scanline: вместо перебора 64 sprites/пиксель — до 8 sprites/scanline.
 *  3. Inline VRAM доступ (минуя ppu_read_vram function call).
 */
void ppu_render_frame(uint32_t *framebuf32) {
  uint8_t *framebuf = (uint8_t*)framebuf32;
  int show_bg = (g_ppumask & 0x08) != 0;
  int show_sp = (g_ppumask & 0x10) != 0;
  int bg_left = (g_ppumask & 0x02) != 0;
  int sp_left = (g_ppumask & 0x04) != 0;

  /* Inline VRAM read (без function call) */
  int m = mapper_get_mirroring();

  for (int y = 0; y < 240; y++) {
    /* ---- Prefetch 32 background tiles для этой scanline ---- */
    int sy = y + g_ppuscroll_y;
    int tile_y = (sy >> 3) & 31;
    int fine_y = sy & 7;
    int nt_base = (g_ppuctrl & 3) << 10;
    int bg_pt = (g_ppuctrl & 0x10) ? 0x1000 : 0;

    /* Cache: tile_idx, attr, pal, b0 (pattern plane 0), b1 (pattern plane 1) */
    uint8_t  tile_idx_cache[32];
    uint8_t  b0_cache[32];
    uint8_t  b1_cache[32];
    uint8_t  pal_cache[32];

    if (show_bg) {
      for (int tx = 0; tx < 32; tx++) {
        int nt_addr = 0x2000 | nt_base | (tile_y << 5) | tx;
        /* inline ppu_read_vram(nt_addr) для nametable */
        uint8_t ti;
        {
          uint16_t a = nt_addr & 0x3FFF;
          if (a < 0x2000) ti = g_chr_ram[a];
          else if (a < 0x3F00) {
            uint16_t off = a & 0x0FFF;
            int nt = (off >> 10) & 3;
            int target;
            if (m == 2) target = nt & 1;
            else if (m == 3) target = (nt >> 1) & 1;
            else target = (m == 0) ? 0 : 1;
            ti = g_ppu_nt[(0x2000 | (target << 10) | (off & 0x03FF)) & 0x0FFF];
          } else ti = g_ppu_pal[a & 0x1F] & 0x3F;
        }
        tile_idx_cache[tx] = ti;
        /* pattern bytes для fine_y */
        uint16_t pa = bg_pt + ti * 16 + fine_y;
        b0_cache[tx] = g_chr_ram[pa];
        b1_cache[tx] = g_chr_ram[pa + 8];
        /* attribute */
        int attr_addr = 0x23C0 | nt_base | ((tile_y >> 2) << 3) | (tx >> 2);
        uint8_t attr;
        {
          uint16_t a = attr_addr & 0x3FFF;
          uint16_t off = a & 0x0FFF;
          int nt = (off >> 10) & 3;
          int target;
          if (m == 2) target = nt & 1;
          else if (m == 3) target = (nt >> 1) & 1;
          else target = (m == 0) ? 0 : 1;
          attr = g_ppu_nt[(0x2000 | (target << 10) | (off & 0x03FF)) & 0x0FFF];
        }
        int shift = ((tile_y & 2) << 1) | (tx & 2);
        pal_cache[tx] = (attr >> shift) & 3;
      }
    }

    /* ---- Build sprite list для этой scanline (до 8 sprites) ---- */
    int sp_count = 0;
    int sp_idx_list[8];
    if (show_sp) {
      for (int i = 0; i < 64 && sp_count < 8; i++) {
        uint8_t sy_oam = g_ppu_oam[i * 4];
        int row = y - sy_oam;
        if (row >= 0 && row < 8) {
          sp_idx_list[sp_count++] = i;
        }
      }
    }

    /* ---- Render scanline ---- */
    for (int x = 0; x < 256; x++) {
      uint8_t pal_idx = 0;
      int has_sprite = 0;
      uint8_t sp_pal = 0;
      int sp_priority = 0;

      /* ---- Background (из cache) ---- */
      if (show_bg && (x >= 8 || bg_left)) {
        int sx = x + g_ppuscroll_x;
        int fine_x = sx & 7;
        int tile_x = (sx >> 3) & 31;
        int bit = 7 - fine_x;
        int lo = (b0_cache[tile_x] >> bit) & 1;
        int hi = (b1_cache[tile_x] >> bit) & 1;
        int color2 = (hi << 1) | lo;
        if (color2) {
          int pal = pal_cache[tile_x];
          pal_idx = g_ppu_pal[(pal << 2) | color2] & 0x3F;
        }
      }

      /* ---- Sprites (из sprite list) ---- */
      if (show_sp && (x >= 8 || sp_left)) {
        int sp_pt = (g_ppuctrl & 0x08) ? 0x1000 : 0;
        for (int si = 0; si < sp_count; si++) {
          int i = sp_idx_list[si];
          uint8_t sx = g_ppu_oam[i * 4 + 3];
          if (x < sx || x >= sx + 8) continue;
          uint8_t tile = g_ppu_oam[i * 4 + 1];
          uint8_t attr = g_ppu_oam[i * 4 + 2];
          int row = y - g_ppu_oam[i * 4];
          int col = x - sx;
          if (attr & 0x40) col = 7 - col;
          if (attr & 0x80) row = 7 - row;
          uint16_t pa = sp_pt + tile * 16 + row;
          uint8_t b0 = g_chr_ram[pa];
          uint8_t b1 = g_chr_ram[pa + 8];
          int bit = 7 - col;
          int lo = (b0 >> bit) & 1;
          int hi = (b1 >> bit) & 1;
          int color2 = (hi << 1) | lo;
          if (color2 == 0) continue;
          int pal = (attr & 3) + 4;
          uint8_t pidx = g_ppu_pal[(pal << 2) | color2] & 0x3F;
          if (!has_sprite || (attr & 0x20) == 0) {
            has_sprite = 1;
            sp_pal = pidx;
            sp_priority = (attr >> 5) & 1;
          }
          break;
        }
      }

      /* ---- Merge ---- */
      uint8_t idx;
      if (has_sprite && (sp_priority == 0 || pal_idx == 0)) {
        idx = sp_pal;
      } else {
        idx = pal_idx;
      }
      uint32_t rgb = s_pal_rgb[idx & 0x3F];
      int i = (y * 256 + x) * 3;
      framebuf[i]     = (rgb >> 16) & 0xFF; /* R */
      framebuf[i + 1] = (rgb >> 8)  & 0xFF; /* G */
      framebuf[i + 2] = rgb        & 0xFF; /* B */
    }
  }
}

void ppu_render_oam_debug(uint32_t *buf) { (void)buf; }
int ppu_predict_spr0_hit_scanline(void) { return 240; }
