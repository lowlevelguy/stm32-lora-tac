#include <stdio.h>

#include "app/lora_app.h"

#include "main.h"
#include "platform.h"
#include "sys_app.h"
#include "radio.h"
#include "stm32_seq.h"
#include "stm32_timer.h"
#include "app/uart_app.h"


/**
 * @brief Critical section macros; expand to no-ops in testing mode.
 */
#ifndef BUILD_TESTING
#define LORA_APP_CRITICAL_SECTION_BEGIN() \
	uint32_t primask = __get_PRIMASK(); __disable_irq()
#define LORA_APP_CRITICAL_SECTION_END()	\
	__set_PRIMASK(primask)
#else
#define LORA_APP_CRITICAL_SECTION_BEGIN() do { (void)0; } while(0)
#define LORA_APP_CRITICAL_SECTION_END() do { (void)0; } while(0)
#endif

/* Bitmasks for ring buffer indexing.
 * Performing a bitwise AND with these masks corresponds to performing a modulo
 * operation with the corresponding LORA_APP_XX_MAX_COUNT value, when it is a
 * power of 2.
 */
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
	RX_ERROR_RX_BUFFER_FULL
};

static RadioEvents_t RadioEvents;

static volatile enum ApplicationState states[LORA_APP_TASK_MAX_COUNT];
static volatile uint8_t state_write_bufidx = 0, state_read_bufidx = 0;

static packet_t rx_pkts[LORA_APP_RX_MAX_COUNT];
static int16_t rssis[LORA_APP_RX_MAX_COUNT];
static int8_t snrs[LORA_APP_RX_MAX_COUNT];
static volatile uint8_t rx_write_bufidx = 0, rx_read_bufidx = 0;

static packet_t tx_pkts[LORA_APP_TX_MAX_COUNT];
static uint8_t tx_retries[LORA_APP_TX_MAX_COUNT];
static volatile uint8_t tx_write_bufidx = 0, tx_read_bufidx = 0;
static volatile bool tx_busy = false;

static volatile enum RxErrorType rx_error[LORA_APP_TASK_MAX_COUNT];

static UTIL_TIMER_Object_t led_timer;

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
 * @brief Command the SubGHz module to perform the head scheduled TX.
 */
static void lora_send() {
	// Trap if the SubGHz TX is busy. This state should never be reached.
	if (tx_busy) {
		lora_trap();
		return;
	}

	LORA_APP_CRITICAL_SECTION_BEGIN();
	// If the ring buffer is empty, no TX is scheduled
	if (tx_write_bufidx == tx_read_bufidx) {
		LORA_APP_CRITICAL_SECTION_END();
		return;
	}
	uint8_t cur_tx_read_bufidx = tx_read_bufidx;

	// Claim TX resource
	tx_busy = true;
	LORA_APP_CRITICAL_SECTION_END();

	Radio.Standby();

	// SubGHz TX Configuration
	Radio.SetChannel(LORA_APP_FREQ);
	Radio.SetTxConfig(MODEM_LORA, LORA_APP_TX_POWER, 0,
		LORA_APP_BW, LORA_APP_SF, LORA_APP_CODINGRATE,
		LORA_APP_PREAMBLE_LENGTH, RADIO_LORA_PACKET_FIXED_LENGTH, RADIO_LORA_CRC_ON,
		false, 0, RADIO_LORA_IQ_NORMAL, LORA_APP_TX_TIMEOUT);
	Radio.SetMaxPayloadLength(MODEM_LORA, LORA_APP_PAYLOAD_LEN);

	Radio.Send((uint8_t*)&tx_pkts[cur_tx_read_bufidx & TX_BUFINDX_MASK], sizeof(packet_t));
}

/**
 * @brief Function to be executed on Radio Tx Done event
 */
static void OnTxDone(void) {
	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Free TX resource
	tx_busy = false;

	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
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
 * @param  rssi unused
 * @param  snr unused
 */
static void OnRxDone(uint8_t* payload, uint16_t size, int16_t rssi, int8_t snr) {
	UNUSED(snr);
	UNUSED(rssi);

	uint8_t cur_state_write_bufidx;
	{
		LORA_APP_CRITICAL_SECTION_BEGIN();
		// Ignore any events on task scheduling exhaustion
		if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
			LORA_APP_CRITICAL_SECTION_END();
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
			// On RX buffer exhaustion, register event as RX_ERROR
			if (rx_write_bufidx - rx_read_bufidx == LORA_APP_RX_MAX_COUNT) {
				LORA_APP_CRITICAL_SECTION_END();

				rx_error[cur_state_write_bufidx & STATE_BUFINDX_MASK] =
					RX_ERROR_RX_BUFFER_FULL;
				states[cur_state_write_bufidx & STATE_BUFINDX_MASK] = RX_ERROR;
				UTIL_SEQ_SetTask(
					LORA_APP_TASK_BASE_ID <<
						(cur_state_write_bufidx & STATE_BUFINDX_MASK),
					CFG_SEQ_Prio_0);
				return;
			}

			cur_rx_write_bufidx = rx_write_bufidx;
			rx_write_bufidx++;
			LORA_APP_CRITICAL_SECTION_END();
		}

		memcpy(&rx_pkts[cur_rx_write_bufidx & RX_BUFINDX_MASK], payload, size);
		rssis[cur_rx_write_bufidx & RX_BUFINDX_MASK] = rssi;
		snrs[cur_rx_write_bufidx & RX_BUFINDX_MASK] = snr;
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
	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Free TX resource
	tx_busy = false;

	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
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
	LORA_APP_CRITICAL_SECTION_BEGIN();
	// Ignore any events on task scheduling exhaustion
	if (state_write_bufidx - state_read_bufidx == LORA_APP_TASK_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
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
 * @brief Function executed on led_timer expiry
 * @param p unused pointer parameter
 */
static void OnLedTimer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LORA_APP_LED_GPIO_PORT, LORA_APP_LED_GPIO_PIN,
					  GPIO_PIN_RESET);
}

/**
 * @brief Main LoRa Application Process
 */
static void lora_app_process(void) {
	switch (states[state_read_bufidx & STATE_BUFINDX_MASK]) {
	case RX_DONE:
		// Blink debug LED
		HAL_GPIO_WritePin(LORA_APP_LED_GPIO_PORT, LORA_APP_LED_GPIO_PIN,
			GPIO_PIN_SET);
		UTIL_TIMER_Start(&led_timer);

		// If the SOF is valid, forward to UART
		packet_t* pkt = &rx_pkts[rx_read_bufidx & RX_BUFINDX_MASK];
		if (pkt->sof == APP_SOF) {
			/* ---- SRS-GW-05 (w/ optional injection for data_type != 1) ---- */
			// Populate data[2-3] with received RSSI and SNR
			// The RSSI is sent with a bias of +200 to fit inside 1 byte
			pkt->data[2] = (uint8_t)(rssis[rx_read_bufidx & RX_BUFINDX_MASK] + 200);
			pkt->data[3] = (uint8_t)snrs[rx_read_bufidx & RX_BUFINDX_MASK];

			/* ---- SRS-GW-01, SRS-GW-02 ---- */
			AppStatus_t s = uart_schedule_send(pkt);

			// If the TX failed to be scheduled, error handle...
			// This is left empty for now
			switch (s) {
			// TX scheduled
			case APP_STATUS_OK:
				break;

			// Could not schedule TX
			case APP_STATUS_ERR_TX_BUFFER_FULL:
			default:
				break;
			}
		}

		rx_read_bufidx++;
		break;

	case RX_ERROR:
		break;

	case TX_DONE:
		// Blink debug LED
		HAL_GPIO_WritePin(LORA_APP_LED_GPIO_PORT, LORA_APP_LED_GPIO_PIN,
			GPIO_PIN_SET);
		UTIL_TIMER_Start(&led_timer);

		// Process next scheduled TX if there are any. Otherwise, go into RX.
		tx_read_bufidx++;
		if (!tx_busy) {
			/*
			 * There is a race condition bug here that we will currently choose to
			 * ignore. If the "TX FIFO empty" test passes, and call to lora_send()
			 * occurs before lora_recv() is ran, the subsequent call to the latter
			 * will halt the TX process before its completion/timeout. This breaks
			 * our design requirement that RX shall never preempt TX.
			 *
			 * TODO:
			 * As the conditions required for this bug to occur are hard to produce
			 * and the fix is quite involved, we leave implementing it for later.
			 */
			if (tx_write_bufidx == tx_read_bufidx) {
				lora_recv();
			} else {
				lora_send();
			}
		}

		break;

	case TX_TIMEOUT:
		// Attempt to retransmit if allowed
		if (tx_retries[tx_read_bufidx & TX_BUFINDX_MASK] <
			LORA_APP_TX_MAX_RETRIES) {
			if (!tx_busy) {
				lora_send();
				tx_retries[tx_read_bufidx & TX_BUFINDX_MASK]++;
			}
		} else {
			// If not, process next scheduled TX, if any, or go back into RX.
			tx_read_bufidx++;
			if (!tx_busy) {
				if (tx_write_bufidx == tx_read_bufidx) {
					lora_recv();
				} else {
					lora_send();
				}
			}
		}
		break;

	// Should never execute
	case UNEXPECTED:
	default:
		lora_trap();
		break;
	}

	state_read_bufidx++;
}

/* Public functions -----------------------------------------------------------*/
void lora_app_init(void) {
	// Initialize LED blinking timers
	UTIL_TIMER_Create(&led_timer, LORA_APP_LED_BLINK_DURATION,
		UTIL_TIMER_ONESHOT, OnLedTimer, NULL);

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

AppStatus_t lora_schedule_send(packet_t* pkt) {
	LORA_APP_CRITICAL_SECTION_BEGIN();
	bool tx_was_empty = (tx_write_bufidx == tx_read_bufidx),
		tx_was_busy = tx_busy;

	// Ignore TX request if the buffer is exhausted
	if (tx_write_bufidx - tx_read_bufidx == LORA_APP_TX_MAX_COUNT) {
		LORA_APP_CRITICAL_SECTION_END();
		return APP_STATUS_ERR_TX_BUFFER_FULL;
	}

	uint8_t cur_tx_write_bufidx = tx_write_bufidx;
	tx_write_bufidx++;

	// Reset retries counter and copy packet into internal TX ring buffer
	tx_retries[cur_tx_write_bufidx & TX_BUFINDX_MASK] = 0;
	memcpy(&tx_pkts[cur_tx_write_bufidx & TX_BUFINDX_MASK],
		pkt, sizeof(packet_t));
	LORA_APP_CRITICAL_SECTION_END();

	if (tx_was_empty && !tx_was_busy) {
		lora_send();
	}
	return APP_STATUS_OK;
}

#ifdef BUILD_TESTING
/* ---- Test accessors -------------------------------------------------------*/

/**
 * @brief Test-only accessors exposing the module's private event callbacks, as
 * well as the main application process.
 *
 * @note Pointer-alias form (rather than forwarder wrappers) was chosen so
 * that the test could, if ever needed, also inspect/replace the function at
 * runtime. Otherwise, both forms reduce to the same call sequence at -O2.
 */
void (*test_OnTxDone)(void)                      = OnTxDone;
void (*test_OnRxDone)(uint8_t*, uint16_t,
                      int16_t, int8_t)           = OnRxDone;
void (*test_OnTxTimeout)(void)                   = OnTxTimeout;
void (*test_OnRxTimeout)(void)                   = OnRxTimeout;
void (*test_OnRxError)(void)                     = OnRxError;
void (*test_OnLedTimer)(void*)                   = OnLedTimer;
void (*test_lora_app_process)(void)              = lora_app_process;
void (*test_lora_send)(void)                     = lora_send;
void (*test_lora_recv)(void)                     = lora_recv;


/**
 * @brief Test-only accessors exposing the module's private state: timer and
 * ring buffers.
 */
UTIL_TIMER_Object_t *test_led_timer              = &led_timer;

/* ---- State Ring Buffer ---- */
volatile enum ApplicationState *test_states      = states;
volatile uint8_t *test_state_write_bufidx        = &state_write_bufidx;
volatile uint8_t *test_state_read_bufidx         = &state_read_bufidx;

/* ---- RX Ring Buffers ---- */
packet_t *test_rx_pkts                           = rx_pkts;
int16_t *test_rssis                              = rssis;
int8_t *test_snrs                                = snrs;
volatile uint8_t *test_rx_write_bufidx           = &rx_write_bufidx;
volatile uint8_t *test_rx_read_bufidx            = &rx_read_bufidx;

/* ---- TX Ring Buffers ---- */
packet_t *test_tx_pkts                           = tx_pkts;
uint8_t *test_tx_retries                         = tx_retries;
volatile uint8_t *test_tx_write_bufidx           = &tx_write_bufidx;
volatile uint8_t *test_tx_read_bufidx            = &tx_read_bufidx;
volatile bool *test_tx_busy                      = &tx_busy;
volatile enum RxErrorType *test_rx_error          = rx_error;

/**
 * @brief Resets every private state variable with no exposed accessor to its
 * initialization value; bar the timer.
 */
void test_reset_lora_app_state(void) {
	state_write_bufidx = 0;
	state_read_bufidx  = 0;
	memset((void*)states, 0, sizeof(states));

	rx_write_bufidx = 0;
	rx_read_bufidx  = 0;
	memset(rx_pkts, 0, sizeof(rx_pkts));
	memset(rssis, 0, sizeof(rssis));
	memset(snrs, 0, sizeof(snrs));

	tx_write_bufidx = 0;
	tx_read_bufidx  = 0;
	memset(tx_pkts, 0, sizeof(tx_pkts));
	memset(tx_retries, 0, sizeof(tx_retries));
	tx_busy = false;

	memset((void*)rx_error, 0, sizeof(rx_error));
}
#endif /* BUILD_TESTING */