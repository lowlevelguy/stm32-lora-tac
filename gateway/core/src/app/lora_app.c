#include "app/lora_app.h"

#include <stdio.h>

#include "platform.h"
#include "sys_app.h"
#include "radio.h"
#include "stm32_seq.h"
#include "stm32_timer.h"
#include "usart.h"


#define LORA_APP_CRITICAL_SECTION_BEGIN() \
	uint32_t primask = __get_PRIMASK(); __disable_irq()
#define LORA_APP_CRITICAL_SECTION_END()	\
	__set_PRIMASK(primask)

#define STATE_BUFINDX_MASK		(LORA_APP_TASK_MAX_COUNT-1)
#define RX_BUFINDX_MASK			(LORA_APP_RX_MAX_COUNT-1)
#define TX_BUFINDX_MASK			(LORA_APP_TX_MAX_COUNT-1)

enum ApplicationState {
	TX_DONE, TX_TIMEOUT,
	RX_DONE, RX_ERROR,
	UNEXPECTED
};

enum RxErrorType {
	RX_ERROR_EXTERNAL,
	RX_ERROR_SIZE_MISMATCH,
};

static RadioEvents_t RadioEvents;

static enum ApplicationState states[LORA_APP_TASK_MAX_COUNT];
static uint8_t state_write_bufidx = 0, state_read_bufidx = 0;

static packet_t rx_pkts[LORA_APP_RX_MAX_COUNT];
static uint8_t rx_write_bufidx = 0, rx_read_bufidx = 0;

static packet_t tx_pkts[LORA_APP_TX_MAX_COUNT];
static uint8_t tx_retries[LORA_APP_TX_MAX_COUNT];
static uint8_t tx_write_bufidx = 0, tx_read_bufidx = 0;

static enum RxErrorType rx_error[LORA_APP_TASK_MAX_COUNT];

static UTIL_TIMER_Object_t tx_led_timer, rx_led_timer;

/* Private functions ---------------------------------------------------------*/
/**
 * @brief Trap into infinite loop when in debug build
 */
static void lora_trap(void) {
#ifdef DEBUG
	while (1) {}
#endif
}

/**
 * @brief Function to be executed on Radio Tx Done event
 */
static void OnTxDone(void) {
	APP_LOG(TS_ON, "OnTxDone\n\r");

	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
		APP_LOG(TS_ON, "LoRa scheduling exhausted.\n\r");
		return;
	}

	uint8_t cur_state_write_bufidx = state_write_bufidx;
	state_write_bufidx++;
	LORA_APP_CRITICAL_SECTION_END();

	states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = TX_DONE;
	UTIL_SEQ_SetTask(
		LORA_APP_TASK_BASE_ID << (cur_state_write_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}

/**
  * @brief Function to be executed on Radio Rx Done event
  * @param  payload ptr of buffer received
  * @param  size buffer size
  * @param  rssi
  * @param  snr
  */
static void OnRxDone(uint8_t* payload, uint16_t size, int16_t rssi, int8_t snr) {
	APP_LOG(TS_ON, "OnRxDone\n\r");

	uint8_t cur_state_write_bufidx;
	{
		LORA_APP_CRITICAL_SECTION_BEGIN();
		// Ignore any events on task scheduling exhaustion
		if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
			LORA_APP_CRITICAL_SECTION_END();
			APP_LOG(TS_ON, "LoRa scheduling exhausted.\n\r");
			return;
		}

		cur_state_write_bufidx = state_write_bufidx;
		state_write_bufidx++;
		LORA_APP_CRITICAL_SECTION_END();
	}

	if (size == LORA_APP_PAYLOAD_LEN) {
		uint8_t cur_rx_write_bufidx;
		{
			LORA_APP_CRITICAL_SECTION_BEGIN();
			// Ignore any events on RX buffers exhaustion
			if (rx_write_bufidx - rx_read_bufidx == LORA_APP_RX_MAX_COUNT) {
				LORA_APP_CRITICAL_SECTION_END();
				APP_LOG(TS_ON, "RX buffers exhausted.\n\r");
				return;
			}

			cur_rx_write_bufidx = rx_write_bufidx;
			rx_write_bufidx++;
			LORA_APP_CRITICAL_SECTION_END();
		}

		memcpy(&rx_pkts[cur_rx_write_bufidx & RX_BUFINDX_MASK], payload, size);
		states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_DONE;
	} else {
		rx_error[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_ERROR_SIZE_MISMATCH;
		states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_ERROR;
	}

	UTIL_SEQ_SetTask(
		LORA_APP_TASK_BASE_ID << (cur_state_write_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Tx Timeout event
  */
static void OnTxTimeout(void) {
	APP_LOG(TS_ON, "OnTxTimeout\n\r");

	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
		APP_LOG(TS_ON, "LoRa scheduling exhausted.\n\r");
		return;
	}

	uint8_t cur_state_write_bufidx = state_write_bufidx;
	state_write_bufidx++;
	LORA_APP_CRITICAL_SECTION_END();

	states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = TX_TIMEOUT;
	UTIL_SEQ_SetTask(
		LORA_APP_TASK_BASE_ID << (cur_state_write_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Rx Timeout event
  * @note Should never execute
  */
static void OnRxTimeout(void) {
	lora_trap();
}

/**
  * @brief Function executed on Radio Rx Error event
  */
static void OnRxError(void) {
	APP_LOG(TS_ON, "OnRxError\n\r");

	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
		APP_LOG(TS_ON, "LoRa scheduling exhausted.\n\r");
		return;
	}

	uint8_t cur_state_write_bufidx = state_write_bufidx;
	state_write_bufidx++;
	LORA_APP_CRITICAL_SECTION_END();

	rx_error[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_ERROR_EXTERNAL;
	states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_ERROR;
	UTIL_SEQ_SetTask(
		LORA_APP_TASK_BASE_ID << (cur_state_write_bufidx & STATE_BUFINDX_MASK),
		CFG_SEQ_Prio_0);
}

/**
 * @brief Make the SubGHz module enter continuous RX mode
 */
static void lora_recv() {
	Radio.Standby();

	// SubGHz RX Configuration
	Radio.SetChannel(LORA_APP_FREQ);
	Radio.SetRxConfig(MODEM_LORA, LORA_APP_BW, LORA_APP_SF, LORA_APP_CODINGRATE,
		0, LORA_APP_PREAMBLE_LENGTH, 0,
		RADIO_LORA_PACKET_FIXED_LENGTH, LORA_APP_PAYLOAD_LEN,
		RADIO_LORA_CRC_ON, false, 0, RADIO_LORA_IQ_NORMAL, true);
	Radio.SetMaxPayloadLength(MODEM_LORA, LORA_APP_PAYLOAD_LEN);

	Radio.Rx(0xFFFFFF);
}

/**
 * @brief Command the SubGHz module to transmit the packet with index
 * bufidx inside the TX packets ring buffer.
 * @param bufidx index of the target packet in the TX packets ring buffer
 */
static void lora_send(uint8_t bufidx) {
	Radio.Standby();

	// SubGHz TX Configuration
	Radio.SetChannel(LORA_APP_FREQ);
	Radio.SetTxConfig(MODEM_LORA, LORA_APP_TX_POWER, 0,
		LORA_APP_BW, LORA_APP_SF, LORA_APP_CODINGRATE,
		LORA_APP_PAYLOAD_LEN, RADIO_LORA_PACKET_FIXED_LENGTH, RADIO_LORA_CRC_ON,
		false, 0, RADIO_LORA_IQ_NORMAL, LORA_APP_TX_TIMEOUT);
	Radio.SetMaxPayloadLength(MODEM_LORA, LORA_APP_PAYLOAD_LEN);

	Radio.Send((uint8_t*)&tx_pkts[bufidx], sizeof(packet_t));
}

/* ---- Debug LED timer callbacks ---- */
/**
 * @brief Function executed on led1_timer expiry
 * @param p unused pointer parameter
 */
static void OnTxLedTimer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LORA_APP_TX_LED_GPIO_PORT, LORA_APP_TX_LED_GPIO_PIN,
					  GPIO_PIN_RESET);
}

/**
 * @brief Function executed on led2_timer expiry
 * @param p unused pointer parameter
 */
static void OnRxLedTimer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LORA_APP_RX_LED_GPIO_PORT, LORA_APP_RX_LED_GPIO_PIN,
	GPIO_PIN_RESET);
}

/**
 * @brief Main Application Process
 */
static void lora_app_process(void) {
	packet_t pkt;

	switch (states[state_read_bufidx]) {
	case RX_DONE:
		// Blink LED for debugging purposes
		HAL_GPIO_WritePin(LORA_APP_RX_LED_GPIO_PORT, LORA_APP_RX_LED_GPIO_PIN, GPIO_PIN_SET);
		UTIL_TIMER_Start(&rx_led_timer);

		// Validate packet SOF
		if (rx_pkts[rx_read_bufidx].sof == APP_SOF) {
			// uart_app_send(&rx_pkts[rx_read_bufidx]);
		}

		rx_read_bufidx = (rx_read_bufidx + 1) % LORA_APP_RX_MAX_COUNT;
		break;

	case RX_ERROR:
		break;

	case TX_DONE:
		// Blink LED for debugging purposes
		HAL_GPIO_WritePin(LORA_APP_TX_LED_GPIO_PORT, LORA_APP_TX_LED_GPIO_PIN, GPIO_PIN_SET);
		UTIL_TIMER_Start(&tx_led_timer);

		// Free TX slot and go back into RX
		tx_read_bufidx = (tx_read_bufidx + 1) % LORA_APP_TX_MAX_COUNT;
		lora_recv();
		break;

	case TX_TIMEOUT:
		// Attempt to retransmit if allowed
		if (tx_retries[tx_read_bufidx] < LORA_APP_TX_MAX_RETRIES) {
			lora_send(tx_read_bufidx);
			tx_retries[tx_read_bufidx]++;
		} else {
			// If not, free TX slot and go back into RX
			tx_read_bufidx = (tx_read_bufidx + 1) % LORA_APP_TX_MAX_COUNT;
			lora_recv();
		}
		break;

	case UNEXPECTED:
	default:
		lora_trap();
		break;
	}

	state_read_bufidx = (state_read_bufidx + 1) % LORA_APP_TASK_MAX_COUNT;
}

/* Public functions -----------------------------------------------------------*/
void lora_app_init(void) {
	char buf[256] = {0};
	snprintf(buf, sizeof(buf),
			"\n\rLoRa PHY Gateway\n\r"
			"Application version: %u.%u\n\r",
			APP_VERSION_MAJOR, APP_VERSION_MINOR);
	HAL_UART_Transmit(&huart2, (uint8_t*)buf, sizeof(buf), HAL_MAX_DELAY);

	// Initialize LED blinking timers
	UTIL_TIMER_Create(&tx_led_timer, LORA_APP_LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
					  OnTxLedTimer, NULL);
	UTIL_TIMER_Create(&rx_led_timer, LORA_APP_LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
					  OnRxLedTimer, NULL);

	// Radio initialization
	RadioEvents.TxDone = OnTxDone;
	RadioEvents.RxDone = OnRxDone;
	RadioEvents.TxTimeout = OnTxTimeout;
	RadioEvents.RxTimeout = OnRxTimeout;
	RadioEvents.RxError = OnRxError;
	Radio.Init(&RadioEvents);

	// Register application process tasks
	for (int i = 0; i < LORA_APP_TASK_MAX_COUNT; i++) {
		UTIL_SEQ_RegTask(LORA_APP_TASK_BASE_ID << i, UTIL_SEQ_DEFAULT,
			lora_app_process);
	}

	// Go into RX mode
	lora_recv();
}

AppStatus_t lora_app_send(packet_t* pkt) {
	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Ignore TX request if the buffer is exhausted
	if (tx_write_bufidx - tx_read_bufidx == LORA_APP_TX_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
		return APP_STATUS_ERR_TX_EXHAUSTED;
	}

	uint8_t cur_tx_write_bufidx = tx_write_bufidx;
	tx_write_bufidx++;
	LORA_APP_CRITICAL_SECTION_END();

	// Reset retries counter and copy packet into internal TX ring buffer
	tx_retries[cur_tx_write_bufidx & TX_BUFINDX_MASK] = 0;
	memcpy(&tx_pkts[cur_tx_write_bufidx & TX_BUFINDX_MASK],
		pkt, sizeof(packet_t));

	lora_send(cur_tx_write_bufidx & TX_BUFINDX_MASK);
	return APP_STATUS_OK;
}