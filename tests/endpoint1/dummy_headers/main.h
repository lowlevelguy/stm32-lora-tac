#ifndef __MOCK_MAIN_H_
#define __MOCK_MAIN_H_

#include "stm32_hal_gpio.h"

/* Macros consumed by endpoint1/core/src/app/commands.c (LED red actuator).
 * The names match the original main.h (uppercase "PORT") so commands.c
 * keeps working untouched. */
#define LED3_PIN         ((uint16_t)0x0800)
#define LED3_GPIO_PORT   ((GPIO_TypeDef*)0x48000400)

/* Macros consumed by endpoint1/core/inc/app/lora_app.h (debug LED aliases).
 * The names match the real main.h (mixed-case "Port") so the SUT's
 *   #define LORA_APP_TX_LED_GPIO_PORT  LED1_GPIO_Port
 *   #define LORA_APP_RX_LED_GPIO_PORT  LED2_GPIO_Port
 * resolve without modification. Distinct dummy addresses so a future test
 * can distinguish which LED was toggled by inspecting the HAL_GPIO_WritePin
 * `GPIOx` argument. */
#define LED1_Pin         ((uint16_t)0x8000)
#define LED1_GPIO_Port   ((GPIO_TypeDef*)0x48000400)
#define LED2_Pin         ((uint16_t)0x0200)
#define LED2_GPIO_Port   ((GPIO_TypeDef*)0x48000404)

#endif