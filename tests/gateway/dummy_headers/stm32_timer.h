#ifndef __MOCK_STM32_TIMER_H_
#define __MOCK_STM32_TIMER_H_

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>   /* NULL -- the SUT passes NULL as the `Argument` param to
                       * UTIL_TIMER_Create; in the production build it reaches
                       * stddef.h transitively via the STM32 HAL / CMSIS chain,
                       * but our test build's include chain does not. */

/* Exported types ------------------------------------------------------------*/

/* Timer mode (lifted from stm32_timer.h:69-72).
 * The SUT passes these as the `Mode` argument to UTIL_TIMER_Create.
 */
typedef enum {
    UTIL_TIMER_ONESHOT  = 0,
    UTIL_TIMER_PERIODIC = 1,
} UTIL_TIMER_Mode_t;

/* Timer API return status (lifted from stm32_timer.h:78-83).
 * The SUT ignores the return values today, but the prototypes must match
 * so the CMock-generated mocks declare the correct signatures.
 */
typedef enum {
    UTIL_TIMER_OK            = 0,
    UTIL_TIMER_INVALID_PARAM = 1,
    UTIL_TIMER_HW_ERROR      = 2,
    UTIL_TIMER_UNKNOWN_ERROR = 3,
} UTIL_TIMER_Status_t;

/* Timer object handle (lifted from stm32_timer.h:88-99).
 *
 * The SUT declares `static UTIL_TIMER_Object_t` instances at file scope
 * and only passes their addresses to the API functions; it never accesses
 * the fields directly. We keep the field layout identical to the production
 * header so a future test that inspects or installs a callback on a captured
 * UTIL_TIMER_Object_t* sees the same memory picture as the firmware would.
 *
 * Note: the production struct declares `UTIL_TIMER_Mode_t Mode`, an enum
 * whose underlying integer width is implementation-defined. We use a plain
 * `uint8_t Mode` here to fix the storage size -- this is safe because the
 * SUT never reads the Mode field directly, only the API functions care
 * about it, and the mock is the API function.
 */
typedef struct TimerEvent_s {
    uint32_t Timestamp;
    uint32_t ReloadValue;
    uint8_t IsPending;
    uint8_t IsRunning;
    uint8_t IsReloadStopped;
    uint8_t Mode;
    void (*Callback)(void *);
    void *argument;
    struct TimerEvent_s *Next;
} UTIL_TIMER_Object_t;

/* Exported functions ------------------------------------------------------- */

/* CMock-mockable. Signature matches the production stm32_timer.h:183. */
UTIL_TIMER_Status_t UTIL_TIMER_Create(UTIL_TIMER_Object_t *TimerObject,
                                      uint32_t PeriodValue,
                                      UTIL_TIMER_Mode_t Mode,
                                      void (*Callback)(void *),
                                      void *Argument);

/* CMock-mockable. Signature matches the production stm32_timer.h:191. */
UTIL_TIMER_Status_t UTIL_TIMER_Start(UTIL_TIMER_Object_t *TimerObject);

#ifdef __cplusplus
}
#endif

#endif /* __MOCK_STM32_TIMER_H_ */
