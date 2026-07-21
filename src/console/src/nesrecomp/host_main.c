/* host_main.c — локальная отладка nesrecomp runner на хосте (x86_64 Linux).
 * Компилируется с обычным gcc, без bare-metal.
 * Запуск: ./mario_host [frames]
 *   frames — сколько frames эмулировать (по умолчанию 600 = 10 сек при 60 FPS).
 */
#include "nes_runtime.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>

extern void runtime_init(void);
extern void func_RESET(void);
extern uint8_t nesrecomp_get_ram(uint16_t addr);

/* Trace callback — то же что в Rust nesrecomp.rs, но для host */
void nesrecomp_trace(uint8_t kind, uint16_t addr, uint8_t val, uint64_t frame, uint64_t extra) {
  switch (kind) {
    case 0: printf("T f=%llu w$2000=%#04x NMI=%d inc32=%d BGpt=%#06x SPpt=%#06x NT=%#06x\n",
              (unsigned long long)frame, val, (val>>7)&1, (val>>2)&1,
              (val>>4)&1?0x1000:0, (val>>3)&1?0x1000:0, ((val&3)<<10)); break;
    case 1: printf("T f=%llu w$2001=%#04x BG=%d SP=%d bgL=%d spL=%d mono=%d\n",
              (unsigned long long)frame, val, (val>>3)&1, (val>>4)&1, (val>>1)&1, (val>>2)&1, val&1); break;
    case 2: printf("T f=%llu w$2005=%#04x (%s write) val=%d\n",
              (unsigned long long)frame, val, extra==0?"scroll_x":"scroll_y", val); break;
    case 3: printf("T f=%llu w$2006=%#04x (%s write)\n",
              (unsigned long long)frame, val, extra==0?"hi":"lo"); break;
    case 4: printf("T f=%llu w$2007=%#04x @ppuaddr=%#06x (after=%#06x)\n",
              (unsigned long long)frame, val, addr, (unsigned)extra); break;
    case 5: printf("T f=%llu r$2002=%#04x VBlank=%d spr0=%d (count=%llu)\n",
              (unsigned long long)frame, val, (val>>7)&1, (val>>6)&1, (unsigned long long)extra); break;
    case 6: printf("T f=%llu r$2007=%#04x @ppuaddr=%#06x (after=%#06x)\n",
              (unsigned long long)frame, val, addr, (unsigned)extra); break;
    case 7: printf("T f=%llu w$0770=%#04x (OperMode) extra=%llu\n",
              (unsigned long long)frame, val, (unsigned long long)extra); break;
    case 8: printf("T f=%llu w$0772=%#04x (OperMode_sub) extra=%llu\n",
              (unsigned long long)frame, val, (unsigned long long)extra); break;
    case 9: printf("T f=%llu w$0774=%#04x (ScreenRoutine) extra=%llu\n",
              (unsigned long long)frame, val, (unsigned long long)extra); break;
    case 10: printf("T f=%llu w$073C=%#04x (GameEngineSub) extra=%llu\n",
              (unsigned long long)frame, val, (unsigned long long)extra); break;
    case 11: printf("T f=%llu NMI_pre  $0772=%#04x $0774=%#04x $0770=%#04x $073C=%#04x ctrl=%#04x mask=%#04x\n",
              (unsigned long long)frame, (addr>>8)&0xFF, addr&0xFF, (val>>4)&0xF, val&0xF,
              (extra>>8)&0xFF, extra&0xFF); break;
    case 12: printf("T f=%llu NMI_post $0772=%#04x $0774=%#04x $0770=%#04x $073C=%#04x ctrl=%#04x mask=%#04x rti_target=%#06x\n",
              (unsigned long long)frame, (addr>>8)&0xFF, addr&0xFF, (val>>4)&0xF, val&0xF,
              (extra>>8)&0xFF, extra&0xFF, (unsigned)(extra>>16)&0xFFFF); break;
    case 13: printf("T f=%llu dispatch_miss addr=%#06x total=%llu\n",
              (unsigned long long)frame, addr, (unsigned long long)extra); break;
    case 14: printf("T f=%llu w$06FC=%#04x (ctrl1 state) $06FD=%#04x\n",
              (unsigned long long)frame, val, (unsigned)extra); break;
    case 15: printf("T f=%llu w$4016=%#04x (strobe) ctrl1_buttons=%#04x\n",
              (unsigned long long)frame, val, (unsigned)extra); break;
    case 16: printf("T f=%llu r$4016=%#04x (strobe=%d)\n",
              (unsigned long long)frame, val, (int)extra); break;
    case 17: printf("T f=%llu w$0779=%#04x (PPUMASK copy) $0774=%#04x\n",
              (unsigned long long)frame, val, (unsigned)extra); break;
    case 18: printf("T f=%llu w$0778=%#04x (PPUCTRL copy) $0774=%#04x\n",
              (unsigned long long)frame, val, (unsigned)extra); break;
    case 19: printf("T f=%llu w$4014=%#04x (OAM DMA) total=%llu\n",
              (unsigned long long)frame, val, (unsigned long long)extra); break;
  }
  fflush(stdout);
}

/* Stub для Rust callback — считает frames, выводит PPU state каждые 60 frames. */
static uint64_t s_frame_count = 0;
static struct timespec s_start;
static int s_max_frames = 600;

void nesrecomp_on_frame(uint64_t frame, const uint8_t *buf, int w, int h) {
  s_frame_count = frame;
  if (frame % 60 == 0 || frame <= 5) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    double elapsed = (now.tv_sec - s_start.tv_sec) + (now.tv_nsec - s_start.tv_nsec) / 1e9;
    double fps = (frame > 0) ? frame / elapsed : 0;
    uint8_t r0779 = nesrecomp_get_ram(0x0779);
    uint8_t r0774 = nesrecomp_get_ram(0x0774);
    uint8_t r0778 = nesrecomp_get_ram(0x0778);
    uint8_t r077a = nesrecomp_get_ram(0x077A);
    uint8_t r0770 = nesrecomp_get_ram(0x0770);
    uint8_t r0772 = nesrecomp_get_ram(0x0772);
    uint8_t r0776 = nesrecomp_get_ram(0x0776);
    uint64_t w2000, w2001, w2005, w2006, w2007, r2002, w4014;
    uint16_t last2001;
    extern uint64_t nesrecomp_get_ppu_counters(uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint64_t*, uint16_t*);
    nesrecomp_get_ppu_counters(&w2000, &w2001, &w2005, &w2006, &w2007, &r2002, &w4014, &last2001);
    printf("frame=%llu fps=%.1f ctrl=%#04x mask=%#04x scroll=(%d,%d) $0779=%#04x $0774=%#04x $0778=%#04x $077A=%#04x $0770=%#04x $0772=%#04x $0776=%#04x\n",
           (unsigned long long)frame, fps,
           g_ppuctrl, g_ppumask, g_ppuscroll_x, g_ppuscroll_y,
           r0779, r0774, r0778, r077a, r0770, r0772, r0776);
    printf("  PPU writes: w2000=%llu w2001=%llu w2005=%llu w2006=%llu w2007=%llu r2002=%llu w4014=%llu last2001=%#04x\n",
           (unsigned long long)w2000, (unsigned long long)w2001,
           (unsigned long long)w2005, (unsigned long long)w2006,
           (unsigned long long)w2007, (unsigned long long)r2002,
           (unsigned long long)w4014, last2001);
    /* Сохраняем framebuffer в PPM каждые 30 frames для проверки */
    if (frame % 30 == 0) {
      char path[64];
      snprintf(path, sizeof(path), "frame_%05llu.ppm", (unsigned long long)frame);
      FILE *f = fopen(path, "wb");
      if (f) {
        fprintf(f, "P6\n%d %d\n255\n", w, h);
        fwrite(buf, 1, (size_t)w * h * 3, f);
        fclose(f);
        /* Считаем уникальные цвета */
        int colors[256] = {0};
        int unique = 0;
        for (int i = 0; i < w * h; i++) {
          uint8_t r = buf[i*3], g = buf[i*3+1], b = buf[i*3+2];
          int idx = (r >> 5) | ((g >> 5) << 3) | ((b >> 5) << 6);
          if (!colors[idx]) { colors[idx] = 1; unique++; }
        }
        printf("  framebuffer %s: %d unique colors (3-bit quantized)\n", path, unique);
      }
    }
  }
  if (frame >= (uint64_t)s_max_frames) {
    printf("Reached %llu frames, exiting.\n", (unsigned long long)frame);
    extern void nesrecomp_dump_0774(void);
    nesrecomp_dump_0774();
    exit(0);
  }
}

int main(int argc, char **argv) {
  if (argc > 1) s_max_frames = atoi(argv[1]);
  clock_gettime(CLOCK_MONOTONIC, &s_start);
  printf("host_main: runtime_init...\n");
  runtime_init();
  /* Установим Start button чтобы game вышла из title screen */
  extern uint8_t g_controller1_buttons;
  g_controller1_buttons = 0x10; /* bit4 = Start */
  printf("host_main: starting RESET (max_frames=%d, ctrl1=Start)\n", s_max_frames);
  func_RESET();
  return 0;
}
