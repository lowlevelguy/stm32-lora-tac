#include "app/lora_app.h"

#include "platform.h"
#include "sys_app.h"
#include "radio.h"
#include "stm32_seq.h"
#include "stm32_timer.h"
#include "usart.h"

#define LED_BLINK_DURATION			100

extern uint8_t btn_press_count;

static RadioEvents_t RadioEvents;

typedef enum {
	TX, TX_DONE, TX_TIMEOUT,
	RX, RX_DONE, RX_TIMEOUT, RX_ERROR,
	ACK_DONE, ACK_TIMEOUT,
	UNEXPECTED
} app_state_t;
app_state_t state;
uint8_t timestamp = 0;

static UTIL_TIMER_Object_t tx_timer, led1_timer, led2_timer, led3_timer;

/* Private functions ---------------------------------------------------------*/
/*!
 * @brief Function to be executed on Radio Tx Done event
 */
static void OnTxDone(void) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxDone\n\r");

	if (state == TX) {
		state = TX_DONE;
	} else if (state == RX_DONE) {
		state = ACK_DONE;
	} else {
		state = UNEXPECTED;
	}

	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
  * @brief Function to be executed on Radio Rx Done event
  * @param  payload ptr of buffer received
  * @param  size buffer size
  * @param  rssi
  * @param  LoraSnr_FskCfo
  */
static void OnRxDone(uint8_t* payload, uint16_t size, int16_t rssi, int8_t LoraSnr_FskCfo) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxTimer\n\r");

	state = RX_DONE;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Tx Timeout event
  */
static void OnTxTimeout(void) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxDone\n\r");

	if (state == TX) {
		state = TX_TIMEOUT;
	} else if (state == RX_DONE) {
		state = ACK_TIMEOUT;
	} else {
		state = UNEXPECTED;
	}

	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Rx Timeout event
  */
static void OnRxTimeout(void) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxTimer\n\r");

	state = RX_TIMEOUT;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Rx Error event
  */
static void OnRxError(void) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxTimer\n\r");

	state = RX_ERROR;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
 * @brief Function executed on tx_timer expiry
 * @param p unused pointer parameter
 */
static void OnTxTimer(void* p) {
	UNUSED(p);
	Radio.Sleep();
	APP_LOG(TS_ON, "OnTxTimer\n\r");

	state = TX;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/* ---- Debug LED timer callbacks ---- */
/**
 * @brief Function executed on led1_timer expiry
 * @param p unused pointer parameter
 */
static void OnLed1Timer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LED1_GPIO_Port, LED1_Pin, GPIO_PIN_RESET);
}

/**
 * @brief Function executed on led2_timer expiry
 * @param p unused pointer parameter
 */
static void OnLed2Timer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LED2_GPIO_PORT, LED2_Pin, GPIO_PIN_RESET);
}

/**
 * @brief Function executed on led3_timer expiry
 * @param p unused pointer parameter
 */
static void OnLed3Timer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LED3_GPIO_Port, LED3_Pin, GPIO_PIN_RESET);
}

/**
 * @brief Main Application Process
 */
static void lora_app_process(void) {
	packet_t pkt = { .sof = LORA_APP_SOF };

	Radio.Sleep();
	APP_LOG(TS_ON, "Inside lora_app_process()\n\r");

	switch (state) {
	case TX:
		// Make LED1 blink for debugging purposes
		HAL_GPIO_WritePin(LORA_APP_TX_LED_GPIO_PORT, LORA_APP_TX_LED_GPIO_PIN, GPIO_PIN_SET);
		UTIL_TIMER_Start(&led1_timer);

		pkt.source_addr = LORA_APP_MY_ADDR;
		pkt.dest_addr = LORA_APP_GATEWAY_ADDR;
		pkt.data_type = PACKET_DATA_TYPE_TELEMETRY;

		/*
		 * Telemetery data format:
		 * data[0]: telemetry type
		 * data[1]: timestamp
		 * data[2]: telemetry value
		 * data[3]: unused; gateway will populate it with received SNR
		 */
		pkt.data[0] = TELEMETRY_TYPE_BUTTON_PRESS_COUNT;
		pkt.data[1] = timestamp++;
		pkt.data[2] = btn_press_count;

		APP_LOG(TS_ON, "Committing telemetry: src_addr(%u), dest_addr(%u), "
				 "telemetry_type(%u), timestamp(%u), telemtry_value(%u).\n\r",
				 pkt.source_addr, pkt.dest_addr,
				 pkt.data[0], pkt.data[1], pkt.data[2]);
		break;
	case TX_DONE:
	case TX_TIMEOUT:
	case RX:
	case RX_DONE:
	case RX_TIMEOUT:
	case RX_ERROR:
	case ACK_DONE:
	case ACK_TIMEOUT:
	default:
		break;
	}
}

/* Public functions -----------------------------------------------------------*/
void lora_app_init(void) {
	APP_LOG(TS_OFF,
		"\n\rLoRa PHY Endpoint 1\n\r"
		"Application version: %u.%u\n\r"
		"Sensors:\n\r"
		"\t- button click counter\n\r"
		"\n\r"
		"Actuators:\n\r"
		"\t- green LED\n\r",
	LORA_APP_VERSION_MAJOR, LORA_APP_VERSION_MINOR);

	// Initialize periodic timer for sensor reports
	UTIL_TIMER_Create(&tx_timer, LORA_APP_TX_PERIOD, UTIL_TIMER_PERIODIC,
		OnTxTimer, NULL);
	UTIL_TIMER_Start(&tx_timer);

	// Initialize LED blinking timers
	UTIL_TIMER_Create(&led1_timer, LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
		OnLed1Timer, NULL);
	UTIL_TIMER_Create(&led2_timer, LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
		OnLed2Timer, NULL);
	UTIL_TIMER_Create(&led3_timer, LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
		OnLed3Timer, NULL);

	// Radio initialization
	RadioEvents.TxDone = OnTxDone;
	RadioEvents.RxDone = OnRxDone;
	RadioEvents.TxTimeout = OnTxTimeout;
	RadioEvents.RxTimeout = OnRxTimeout;
	RadioEvents.RxError = OnRxError;
	Radio.Init(&RadioEvents);

	// Register application process task
	UTIL_SEQ_RegTask(LORA_APP_TASK_ID, UTIL_SEQ_DEFAULT, lora_app_process);
}
