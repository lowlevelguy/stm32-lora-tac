#include "platform.h"
#include "sys_app.h"
#include "app/lora_app.h"
#include "radio.h"
#include "stm32_timer.h"
#include "usart.h"

static RadioEvents_t RadioEvents;
static UTIL_TIMER_Object_t tx_timer;

/* Private functions ---------------------------------------------------------*/
/*!
 * @brief Function to be executed on Radio Tx Done event
 */
static void OnTxDone(void) {

}

/**
  * @brief Function to be executed on Radio Rx Done event
  * @param  payload ptr of buffer received
  * @param  size buffer size
  * @param  rssi
  * @param  LoraSnr_FskCfo
  */
static void OnRxDone(uint8_t* payload, uint16_t size, int16_t rssi, int8_t LoraSnr_FskCfo) {

}

/**
  * @brief Function executed on Radio Tx Timeout event
  */
static void OnTxTimeout(void) {

}

/**
  * @brief Function executed on Radio Rx Timeout event
  */
static void OnRxTimeout(void) {

}

/**
  * @brief Function executed on Radio Rx Error event
  */
static void OnRxError(void) {

}

/* USER CODE BEGIN PFP */
static void OnTxTimer(void* p) {
	UNUSED(p);
	uint8_t buf[] = "Hello World\n\r";
	HAL_StatusTypeDef s = HAL_UART_Transmit(&huart2, buf, sizeof(buf), 1000);
	HAL_GPIO_TogglePin(GPIOB, GPIO_PIN_9);
}

/* Public functions -----------------------------------------------------------*/
void lora_app_init(void) {
	UTIL_TIMER_Create(&tx_timer, 500, UTIL_TIMER_PERIODIC, OnTxTimer, NULL);
	UTIL_TIMER_Start(&tx_timer);

	/* Radio initialization */
	RadioEvents.TxDone = OnTxDone;
	RadioEvents.RxDone = OnRxDone;
	RadioEvents.TxTimeout = OnTxTimeout;
	RadioEvents.RxTimeout = OnRxTimeout;
	RadioEvents.RxError = OnRxError;

	Radio.Init(&RadioEvents);
}
