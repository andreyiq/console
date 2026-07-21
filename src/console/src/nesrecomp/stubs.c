/* stubs.c — stubs для coroutine/recomp_stack/reverse_debug/watchdog/debug_server */
#include "coroutine.h"
#include "recomp_stack.h"
#include "reverse_debug.h"
#include "watchdog.h"
#include "debug_server.h"
#include "game_extras.h"
#include "nes_runtime.h"

/* ---- coroutine ---- */
int coroutine_scheduler_setjmp(void) { return 0; }
void coroutine_yield(void) {}
void coroutine_resume(int channel) { (void)channel; }
void coroutine_set_channel(int channel) { (void)channel; }
void coroutine_start(int channel, uint16_t addr) { (void)channel; (void)addr; }
int  coroutine_is_active(void) { return 0; }
int  coroutine_has_context(int channel) { (void)channel; return 0; }
void coroutine_get_debug_counters(int *y, int *r, int *s, uint8_t *ysp, uint8_t *rsp) {
  *y = 0; *r = 0; *s = 0; *ysp = 0; *rsp = 0;
}
void coroutine_get_sched_trace(int *c, int *i) { *c = 0; *i = 0; }
const SchedTraceEntry *coroutine_get_sched_trace_buf(void) { return 0; }
int  coroutine_get_current_channel(void) { return -1; }
int  coroutine_restart_requested(void) { return 0; }

/* ---- recomp_stack ---- */
const char *g_recomp_stack[512];
int        g_recomp_stack_top = 0;
const char *g_last_recomp_func = "";
void recomp_stack_push(const char *name) { (void)name; }
void recomp_stack_pop(void) {}

/* ---- reverse_debug (NESRECOMP_REVERSE_DEBUG=0 → rdb_on_call inline) ---- */
uint16_t g_rdb_current_func = 0;
volatile int g_rdb_paused = 0;
void rdb_wait_if_parked(void) {}
int  rdb_handle_cmd(const char *cmd, int id, const char *json) { (void)cmd; (void)id; (void)json; return 0; }
void rdb_init(void) {}

/* ---- watchdog ---- */
#include <setjmp.h>
jmp_buf g_watchdog_jmp;
void watchdog_check(void) {}
void watchdog_frame_start(void) {}

/* ---- debug_server stubs ---- */
void debug_server_init(int port) { (void)port; }
void debug_server_wait_if_paused(void) {}
void debug_server_poll(void) {}

/* ---- game_extras (stub) ---- */
const char *game_get_name(void) { return "Mario (bare-metal)"; }
void game_on_init(void) {}
void game_on_frame(uint64_t fc) { (void)fc; }
void game_post_nmi(uint64_t fc) { (void)fc; }
int  game_handle_arg(const char *k, const char *v) { (void)k; (void)v; return 0; }
const char *game_arg_usage(void) { return 0; }
uint32_t game_get_expected_crc32(void) { return 0; }
int  game_dispatch_override(uint16_t addr) { (void)addr; return 0; }
