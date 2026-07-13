#include "app/uart_app.h"

#include <string.h>

#include "stm32wlxx_hal.h"
#include "stm32_seq.h"
#include "stm32_timer.h"
#include "app/lora_app.h"


#define UART_APP_CRITICAL_SECTION_BEGIN() \
uint32_t primask = __get_PRIMASK(); __disable_irq()
#define UART_APP_CRITICAL_SECTION_END() \
__set_PRIMASK(primask)

/* Bitmasks for ring buffer indexing.
 * Performing a bitwise AND with these masks corresponds to performing a modulo
 * operation with the corresponding UART_APP_XX_MAX_COUNT value, when it is a
 * power of 2.
 */
#define STATE_BUFINDX_MASK		(UART_APP_TASK_MAX_COUNT-1)
#define RX_BUFINDX_MASK			(UART_APP_RX_MAX_COUNT-1)
#define TX_BUFINDX_MASK			(UART_APP_TX_MAX_COUNT-1)


enum ApplicationState {
	TX_DONE, TX_TIMEOUT,
	RX_DONE,
};

extern UART_HandleTypeDef huart2;

static UTIL_TIMER_Object_t tx_timeout_timer;

static enum ApplicationState state[UART_APP_TASK_MAX_COUNT];
static volatile uint8_t state_write_bufidx = 0, state_read_bufidx = 0;

static packet_t rx_pkts[UART_APP_RX_MAX_COUNT];
static volatile uint8_t rx_write_bufidx = 0, rx_read_bufidx = 0;

static packet_t tx_pkts[UART_APP_TX_MAX_COUNT];
static uint8_t tx_retries[UART_APP_TX_MAX_COUNT];
static volatile uint8_t tx_write_bufidx = 0, tx_read_bufidx = 0;
static volatile bool tx_busy = false;

static UTIL_TIMER_Object_t led_timer;

/* Private functions ---------------------------------------------------------*/
/**
 * @brief Trap into infinite loop when in debug build
 */
static void uart_trap(void) {
#ifdef DEBUG
	while (1) {}
#endif
}

/**
 * @brief Command the UART interface to perform the head scheduled TX.
 */
static void uart_send(void) {
	// Trap if the UART TX is busy. This state should never be reached.
	if (tx_busy) {
		uart_trap();
	}

	UART_APP_CRITICAL_SECTION_BEGIN();
	// If the TX buffer is empty, no TX is scheduled
	if (tx_write_bufidx == tx_read_bufidx) {
		UART_APP_CRITICAL_SECTION_END();
		return;
	}
	uint8_t cur_tx_read_bufidx = tx_read_bufidx;

	// Claim TX resource
	tx_busy = true;
	UART_APP_CRITICAL_SECTION_END();

	// Start timeout timer and begin TX
	UTIL_TIMER_Start(&tx_timeout_timer);
	HAL_StatusTypeDef s = HAL_UART_Transmit_IT(&huart2,
		(uint8_t*)&tx_pkts[cur_tx_read_bufidx & TX_BUFINDX_MASK],
		UART_APP_PAYLOAD_LEN);

	// Trap if UART TX was being flagged as unbusy in our firmware, yet is busy in reality
	if (s != HAL_OK) {
		uart_trap();
	}
}

/**
 * @brief Function executed on UART packet transmission completion
 */
static inline void OnTxDone(void) {
	// ISR-safe ring buffer indexing
	UART_APP_CRITICAL_SECTION_BEGIN();
	// Free TX resource
	tx_busy = false;

	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == UART_APP_TASK_MAX_COUNT) {
		UART_APP_CRITICAL_SECTION_END();
		return;
	}

	uint8_t cur_state_bufidx = state_write_bufidx;
	state_write_bufidx++;
	UART_APP_CRITICAL_SECTION_END();

	// Reset TX timeout timer
	UTIL_TIMER_Stop(&tx_timeout_timer);

	// Set FSM and schedule task
	state[cur_state_bufidx & STATE_BUFINDX_MASK] = TX_DONE;
	UTIL_SEQ_SetTask(
		UART_APP_TASK_BASE_ID << (cur_state_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}

/**
 * @brief Function executed on UART packet reception completion
 */
static inline void OnRxDone(void) {
	// Thread-safe ring buffer indexing
	UART_APP_CRITICAL_SECTION_BEGIN();
	if (state_write_bufidx - state_read_bufidx == UART_APP_TASK_MAX_COUNT ||
		rx_write_bufidx - rx_read_bufidx == UART_APP_RX_MAX_COUNT) {
		UART_APP_CRITICAL_SECTION_END();
		return;
	}

	uint8_t cur_state_bufidx = state_write_bufidx,
		cur_rx_bufidx = rx_write_bufidx;
	state_write_bufidx++;
	rx_write_bufidx++;
	UART_APP_CRITICAL_SECTION_END();

	state[cur_state_bufidx & STATE_BUFINDX_MASK] = RX_DONE;
	UTIL_SEQ_SetTask(
		UART_APP_TASK_BASE_ID << (cur_state_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);

	HAL_StatusTypeDef s = HAL_UART_Receive_IT(&huart2,
		(uint8_t*)&rx_pkts[cur_rx_bufidx & RX_BUFINDX_MASK],
		UART_APP_PAYLOAD_LEN);
	if (s != HAL_OK) {
		uart_trap();
	}
}

/**
 * @brief Function executed on tx_timeout_timer expiry
 * @param unused unused parameter
 */
static void OnTxTimeout(void* unused) {
	UNUSED(unused);
	// Abort TX
	HAL_UART_AbortTransmit_IT(&huart2);

	// Thread-safe ring buffer indexing
	UART_APP_CRITICAL_SECTION_BEGIN();
	// Free TX resource
	tx_busy = false;

	if (state_write_bufidx - state_read_bufidx == UART_APP_TASK_MAX_COUNT) {
		UART_APP_CRITICAL_SECTION_END();
		return;
	}

	uint8_t cur_write_bufidx = state_write_bufidx;
	state_write_bufidx++;
	UART_APP_CRITICAL_SECTION_END();

	state[cur_write_bufidx & STATE_BUFINDX_MASK] = TX_TIMEOUT;
	UTIL_SEQ_SetTask(
		UART_APP_TASK_BASE_ID << (cur_write_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}


/**
 * @brief Function executed on led_timer expiry
 * @param p unused pointer parameter
 */
static void OnLedTimer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(UART_APP_LED_GPIO_PORT, UART_APP_LED_GPIO_PIN,
					  GPIO_PIN_RESET);
}

/**
 * @brief Main UART Application Process
 */
static void uart_app_process(void) {
	switch (state[state_read_bufidx & STATE_BUFINDX_MASK]) {
	case TX_DONE:
		// Blink debug LED
		HAL_GPIO_WritePin(UART_APP_LED_GPIO_PORT, UART_APP_LED_GPIO_PIN,
			GPIO_PIN_SET);
		// Reset timer if it's already running
		if (UTIL_TIMER_IsRunning(&led_timer)) {
			UTIL_TIMER_Stop(&led_timer);
		}
		UTIL_TIMER_Start(&led_timer);

		tx_read_bufidx++;
		if (tx_write_bufidx != tx_read_bufidx) {
			if (!tx_busy) {
				uart_send();
			}
		}
		break;

	case TX_TIMEOUT:
		if (tx_retries[tx_read_bufidx & TX_BUFINDX_MASK] <
			UART_APP_TX_MAX_RETRIES) {
			if (!tx_busy) {
				/*
				 * There is a race condition on tx_retries here. If OnTxDone
				 * fires before the increment finishes, tx_read_bufidx will
				 * increment within the
				 */
				uart_send();
				tx_retries[tx_read_bufidx & TX_BUFINDX_MASK]++;
			}
		} else {
			tx_read_bufidx++;
			if (tx_write_bufidx != tx_read_bufidx) {
				if (!tx_busy) {
					uart_send();
				}
			}
		}
		break;

	case RX_DONE:
		// Blink debug LED
		HAL_GPIO_WritePin(UART_APP_LED_GPIO_PORT, UART_APP_LED_GPIO_PIN,
			GPIO_PIN_SET);
		// Reset timer if it's already running
		if (UTIL_TIMER_IsRunning(&led_timer)) {
			UTIL_TIMER_Stop(&led_timer);
		}
		UTIL_TIMER_Start(&led_timer);

		// If the SOF is valid, forward to LoRa
		if (rx_pkts[rx_read_bufidx & RX_BUFINDX_MASK].sof == APP_SOF) {
			lora_schedule_send(&rx_pkts[rx_read_bufidx & RX_BUFINDX_MASK]);
		}

		rx_read_bufidx++;
		break;

	// Trap if an unexpected state is reached
	default:
		uart_trap();
		break;
	}

	state_read_bufidx++;
}

/* Public functions ----------------------------------------------------------*/
void uart_app_init() {
	/* Initializing indices
	 * Though they were already initialized correctly at declaration, this is
	 * just fallback in case the initialization are mistakenly removed later on.
	 */
	state_read_bufidx = 0;
	state_write_bufidx = 0;
	rx_read_bufidx = 0;
	rx_write_bufidx = 0;
	tx_read_bufidx = 0;
	tx_write_bufidx = 0;

	// Initialize TX timeout timer
	UTIL_TIMER_Create(&tx_timeout_timer, UART_APP_TX_TIMEOUT,
		UTIL_TIMER_ONESHOT, OnTxTimeout, NULL);

	// Initialize debug LED timer
	UTIL_TIMER_Create(&led_timer, UART_APP_LED_BLINK_DURATION,
		UTIL_TIMER_ONESHOT, OnLedTimer, NULL);

	// Register sequencer tasks
	for (int i = 0; i < UART_APP_TASK_MAX_COUNT; i++) {
		UTIL_SEQ_RegTask(UART_APP_TASK_BASE_ID << i, UTIL_SEQ_DEFAULT,
			uart_app_process);
	}

	// Begin RX
	HAL_StatusTypeDef s = HAL_UART_Receive_IT(&huart2,
		(uint8_t*)&rx_pkts[0],
		UART_APP_PAYLOAD_LEN);
	if (s != HAL_OK) {
		uart_trap();
	}
}

AppStatus_t uart_schedule_send(packet_t* pkt) {
	// Thread-safe ring buffer indexing
	UART_APP_CRITICAL_SECTION_BEGIN();
	bool was_empty = (tx_write_bufidx == tx_read_bufidx),
		was_busy = tx_busy;

	if (tx_write_bufidx - tx_read_bufidx == UART_APP_TX_MAX_COUNT) {
		UART_APP_CRITICAL_SECTION_END();
		return APP_STATUS_ERR_TX_BUFFER_FULL;
	}

	uint8_t cur_write_bufidx = tx_write_bufidx;
	tx_write_bufidx++;

	// Reset retries counter and copy packet into internal TX ring buffer
	tx_retries[cur_write_bufidx & TX_BUFINDX_MASK] = 0;
	memcpy(&tx_pkts[cur_write_bufidx & TX_BUFINDX_MASK],
		pkt, UART_APP_PAYLOAD_LEN);
	UART_APP_CRITICAL_SECTION_END();

	if (was_empty && !was_busy) {
		uart_send();
	}
	return APP_STATUS_OK;
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef* huart) {
	if (huart->Instance == USART2) {
		OnTxDone();
	}
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef* huart) {
	if (huart->Instance == USART2) {
		OnRxDone();
	}
}