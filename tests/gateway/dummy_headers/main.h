#ifndef __MOCK_MAIN_H_
#define __MOCK_MAIN_H_

#include <stdbool.h>
#include <string.h>

#include "stm32_hal_gpio.h"

/**
 * @brief Stub LED GPIO port and pin macros.
 *
 * @note Though the values used match the real addresses, that's not really
 * necessary.
 */
#define LED1_Pin		((uint16_t)0x8000)
#define LED1_GPIO_Port	((GPIO_TypeDef*)0x48000400)
#define LED2_Pin		((uint16_t)0x0200)
#define LED2_GPIO_Port	((GPIO_TypeDef*)0x48000404)
#define LED3_Pin		((uint16_t)0x0800)
#define LED3_GPIO_Port	((GPIO_TypeDef*)0x48000400)

#define UNUSED(X)		((void)(X))

#endif