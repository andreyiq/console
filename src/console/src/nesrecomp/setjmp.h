/* setjmp.h — минимальная заглушка для bare-metal (coroutine stub не использует longjmp) */
#pragma once
typedef int jmp_buf[1];
