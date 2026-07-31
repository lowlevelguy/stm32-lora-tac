#ifndef __MOCK_STM32_SEQ_H_
#define __MOCK_STM32_SEQ_H_

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include <stdint.h>

/* Exported types ------------------------------------------------------------*/

/* Bitmask type used to identify tasks and priorities.
 * Lifted from the production stm32_seq.h:44. */
typedef uint32_t UTIL_SEQ_bm_t;

/* Exported constants --------------------------------------------------------*/

/* Default task-priority constant consumed by the SUT's
 *   UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
 *
 * In the production header chain this lives in utilities_def.h:64, not
 * stm32_seq.h itself. We hoist it into this dummy so the SUT's
 * `#include "stm32_seq.h"` is self-sufficient -- no separate dummy for
 * utilities_def.h is required.
 *
 * The real enum value is 0; replicating that as a #define matches the
 * numeric expression the SUT actually compiles against. */
#define CFG_SEQ_Prio_0 0

/* Mask used by the application's main loop to run all registered tasks.
 * Lifted from stm32_seq.h:75. The SUT's own lora_app.c never calls
 * UTIL_SEQ_Run, but main.c does; defining the macro here keeps the dummy
 * useful for any future test that exercises the run-loop. */
#define UTIL_SEQ_DEFAULT  (~0U)

/* Exported functions ------------------------------------------------------- */

/* CMock-mockable. Signature matches the production stm32_seq.h:212. */
void UTIL_SEQ_RegTask(UTIL_SEQ_bm_t TaskId_bm, uint32_t Flags, void (*Task)(void));

/* CMock-mockable. Signature matches the production stm32_seq.h:227. */
void UTIL_SEQ_SetTask(UTIL_SEQ_bm_t TaskId_bm, uint32_t Task_Prio);

#ifdef __cplusplus
}
#endif

#endif /* __MOCK_STM32_SEQ_H_ */
