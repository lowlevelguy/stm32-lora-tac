#ifndef __MOCK_PLATFORM_H__
#define __MOCK_PLATFORM_H__

#ifdef __cplusplus
extern "C" {
#endif

/* Minimal stub for the production platform.h.
 *
 * The real header pulls in the STM32WL CMSIS device header, LL GPIO
 * headers, and Nucleo BSP headers -- none of which exist on the host
 * unit-test build. Inspection of the SUT (lora_app.c) shows it does not
 * consume any symbol from platform.h directly; the SUT's `#include
 * "platform.h"` is CubeMX-template defensive coding that is satisfied by
 * an empty stub on the host.
 *
 * Should a future SUT change introduce a real dependency on a symbol
 * that currently resolves transitively through platform.h, the compiler
 * diagnostic will direct you to add the missing symbol here (or to a
 * more specific dummy header). */
#include <stdbool.h>

#ifdef __cplusplus
}
#endif

#endif /* __MOCK_PLATFORM_H__ */
