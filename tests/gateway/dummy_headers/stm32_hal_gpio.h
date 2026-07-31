#ifndef __MOCK_STM32_HAL_GPIO_H_
#define __MOCK_STM32_HAL_GPIO_H_

#include <stdint.h>

typedef struct { uint32_t dummy; } GPIO_TypeDef;
typedef enum { GPIO_PIN_RESET = 0, GPIO_PIN_SET = 1 } GPIO_PinState;

void HAL_GPIO_WritePin(GPIO_TypeDef* GPIOx, uint16_t GPIO_Pin, GPIO_PinState PinState);
uint32_t HAL_GetTick(void);

#endif