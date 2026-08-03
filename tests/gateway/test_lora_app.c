#include <unity.h>
#include <string.h>

#include "app/lora_app.h"
#include "app/app_state.h"

#include "main.h"
#include "radio.h"

#include "mock_stm32_hal_gpio.h"
#include "mock_stm32_seq.h"
#include "mock_stm32_timer.h"


/* Test-only SUT accessors ---------------------------------------------------*/
extern void (*test_OnTxDone)(void);
extern void (*test_OnRxDone)(uint8_t*, uint16_t, int16_t, int8_t);
extern void (*test_OnTxTimeout)(void);
extern void (*test_OnRxTimeout)(void);
extern void (*test_OnRxError)(void);
extern void (*test_OnLedTimer)(void*);
extern void (*test_lora_app_process)(void);
extern void (*test_lora_send)(void);
extern void (*test_lora_recv)(void);

extern UTIL_TIMER_Object_t *test_led_timer;

extern volatile enum ApplicationState *test_states;
extern volatile uint8_t *test_state_write_bufidx;
extern volatile uint8_t *test_state_read_bufidx;

extern packet_t *test_rx_pkts;
extern int16_t *test_rssis;
extern int8_t *test_snrs;
extern volatile uint8_t *test_rx_write_bufidx;
extern volatile uint8_t *test_rx_read_bufidx;

extern packet_t *test_tx_pkts;
extern uint8_t *test_tx_retries;
extern volatile uint8_t *test_tx_write_bufidx;
extern volatile uint8_t *test_tx_read_bufidx;
extern volatile bool *test_tx_busy;
extern volatile enum RxErrorType *test_rx_error;

extern void test_reset_lora_app_state(void);


/* Radio History -------------------------------------------------------------*/
static struct {
	packet_t      last_packet;
	uint16_t      last_size;
	bool		  last_rx_continuous;
	RadioEvents_t last_init_events;
	bool          init_events_captured;
	uint32_t      send_count;
	uint32_t      rx_count;
	uint32_t      sleep_count;
	uint32_t      standby_count;
	uint32_t      init_count;
	uint32_t      set_channel_count;
	uint32_t      set_rx_config_count;
	uint32_t      set_tx_config_count;
	uint32_t      set_max_payload_length_count;
} radio_history;


/* UART History -------------------------------------------------------------*/
static struct {
	packet_t   last_packet;
	uint32_t   call_count;
	AppStatus_t next_return;
} uart_history;


/* Stub Radio Driver ---------------------------------------------------------*/
/*
 * The production `Radio` is a const function-pointer struct supplied by
 * the SubGHz driver; CMock cannot mock function-pointer structs. We
 * instead provide a hand-written instance whose members are recording
 * stubs. Each stub increments its counter and (where useful) snapshots
 * the args into `radio_history` for later assertion.
 *
 * Signatures match `struct Radio_s` in tests/gateway/dummy_headers/radio.h
 * exactly.
 */

static void fake_Radio_Init(RadioEvents_t *events) {
	UNUSED(events);
	radio_history.init_count++;
	if (events != NULL) {
		memcpy(&radio_history.last_init_events, events,
		       sizeof(RadioEvents_t));
		radio_history.init_events_captured = true;
	}
}

static void fake_Radio_SetChannel(uint32_t freq) {
	UNUSED(freq);
	radio_history.set_channel_count++;
}

static void fake_Radio_SetRxConfig(RadioModems_t modem, uint32_t bandwidth,
                                   uint32_t datarate, uint8_t coderate,
                                   uint32_t bandwidthAfc,
                                   uint16_t preambleLen,
                                   uint16_t symbTimeout, bool fixLen,
                                   uint8_t payloadLen, bool crcOn,
                                   bool freqHopOn, uint8_t hopPeriod,
                                   bool iqInverted, bool rxContinuous) {
	UNUSED(modem);        UNUSED(bandwidth);      UNUSED(datarate);
	UNUSED(coderate);     UNUSED(bandwidthAfc);   UNUSED(preambleLen);
	UNUSED(symbTimeout);  UNUSED(fixLen);         UNUSED(payloadLen);
	UNUSED(crcOn);        UNUSED(freqHopOn);      UNUSED(hopPeriod);
	UNUSED(iqInverted);
	radio_history.last_rx_continuous = rxContinuous;
	radio_history.set_rx_config_count++;
}

static void fake_Radio_SetTxConfig(RadioModems_t modem, int8_t power,
                                   uint32_t fdev, uint32_t bandwidth,
                                   uint32_t datarate, uint8_t coderate,
                                   uint16_t preambleLen, bool fixLen,
                                   bool crcOn, bool freqHopOn,
                                   uint8_t hopPeriod, bool iqInverted,
                                   uint32_t timeout) {
	UNUSED(modem);        UNUSED(power);       UNUSED(fdev);
	UNUSED(bandwidth);    UNUSED(datarate);    UNUSED(coderate);
	UNUSED(preambleLen);  UNUSED(fixLen);      UNUSED(crcOn);
	UNUSED(freqHopOn);    UNUSED(hopPeriod);   UNUSED(iqInverted);
	UNUSED(timeout);
	radio_history.set_tx_config_count++;
}

static radio_status_t fake_Radio_Send(uint8_t *buffer, uint8_t size) {
	UNUSED(buffer);
	UNUSED(size);
	radio_history.last_size = size;
	if (buffer != NULL && size >= sizeof(packet_t)) {
		memcpy(&radio_history.last_packet, buffer, sizeof(packet_t));
	}
	radio_history.send_count++;
	return RADIO_STATUS_OK;
}

static void fake_Radio_Standby(void) {
	radio_history.standby_count++;
}

static void fake_Radio_Rx(uint32_t timeout) {
	UNUSED(timeout);
	radio_history.rx_count++;
}

static void fake_Radio_SetMaxPayloadLength(RadioModems_t modem, uint8_t max) {
	UNUSED(modem);
	UNUSED(max);
	radio_history.set_max_payload_length_count++;
}

/* Stub Cross-SUT: uart_schedule_send ---------------------------------------*/
/*
 * The SUT calls uart_schedule_send(pkt) from lora_app_process's RX_DONE
 * branch for valid-SOF packets. We provide a hand-written stub rather
 * than a CMock mock because:
 *   (a) lora_app.c cross-#includes "app/uart_app.h", which resolves to
 *       the production header in gateway/core/inc/app/ (declaring both
 *       uart_app_init and uart_schedule_send). Creating a dummy
 *       shadow at tests/gateway/dummy_headers/app/uart_app.h to feed
 *       CMock would later collide with the sibling uart_app session's
 *       own test target (both want to be the authoritative uart_app.h
 *       but neither can serve both roles).
 *   (b) CMock-mocking the production uart_app.h would force every
 *       test's setUp to _Ignore uart_app_init (which the SUT never
 *       calls but CMock would nonetheless generate stubs for).
 *
 * The stub mirrors the fake_Radio_* pattern: capture args into
 * uart_history, return uart_history.next_return (defaults to
 * APP_STATUS_OK after reset).
 */
static AppStatus_t fake_uart_schedule_send(packet_t *pkt) {
	UNUSED(pkt);
	uart_history.call_count++;
	if (pkt != NULL) {
		memcpy(&uart_history.last_packet, pkt, sizeof(packet_t));
	}
	return uart_history.next_return;
}

/*
 * Definition of the cross-SUT symbol. The SUT declares
 * uart_schedule_send external (via gateway/core/inc/app/uart_app.h);
 * we provide the host-test definition here. The companion uart_app
 * SUT's .c is NOT in this test target's SOURCES list (see
 * tests/CMakeLists.txt gateway_test_lora_app entry), so there is no
 * duplicate-definition conflict.
 */
AppStatus_t uart_schedule_send(packet_t *pkt) {
	return fake_uart_schedule_send(pkt);
}

const struct Radio_s Radio = {
	.Init                = fake_Radio_Init,
	.SetChannel          = fake_Radio_SetChannel,
	.SetRxConfig         = fake_Radio_SetRxConfig,
	.SetTxConfig         = fake_Radio_SetTxConfig,
	.Send                = fake_Radio_Send,
	.Standby             = fake_Radio_Standby,
	.Rx                  = fake_Radio_Rx,
	.SetMaxPayloadLength = fake_Radio_SetMaxPayloadLength,
};


/* Test State Reset ----------------------------------------------------------*/
/**
 * @brief Reset test state: radio history and all SUT file-scope mutables.
 *
 * Called from setUp() so each RUN_TEST starts from a known baseline.
 */
static void reset_test_state(void) {
	memset(&radio_history, 0, sizeof(radio_history));
	memset(&uart_history, 0, sizeof(uart_history));
	test_reset_lora_app_state();
}


/* Unity Harness ------------------------------------------------------------*/
void setUp(void) {
	mock_stm32_hal_gpio_Init();
	mock_stm32_seq_Init();
	mock_stm32_timer_Init();
	reset_test_state();
}

void tearDown(void) {
	mock_stm32_hal_gpio_Verify();
	mock_stm32_hal_gpio_Destroy();

	mock_stm32_seq_Verify();
	mock_stm32_seq_Destroy();

	mock_stm32_timer_Verify();
	mock_stm32_timer_Destroy();
}


/* Unit Tests ----------------------------------------------------------------*/
/**
 * @brief Application configuration test.
 *
 * @note Partially covers SRS-GW-01, and ensures the LoRa modulation params
 * match those specified for the endpoint in SRS-ED-06.
 */
void test_application_configuration_matches_srs(void) {
	/* ---- SRS-GW-01 ---- */
	TEST_ASSERT_EQUAL_UINT8(0xA5, APP_SOF);

	/* ---- SRS-ED-06 ---- */
	TEST_ASSERT_EQUAL_UINT32(433000000, LORA_APP_FREQ);
	TEST_ASSERT_EQUAL_UINT32(0, LORA_APP_BW);			// 125kHz BW maps to 0
	TEST_ASSERT_EQUAL_UINT32(7, LORA_APP_SF);
	TEST_ASSERT_EQUAL_UINT32(1, LORA_APP_CODINGRATE);	// 4/5 coding rate maps to 1
	TEST_ASSERT_EQUAL_UINT8(14, LORA_APP_TX_POWER);
}

/**
 * @brief Test LoRa app initialization.
 */
void test_lora_app_init_registers_tasks_inits_radio_and_starts_rx(void) {
	// Ignore debug LED
	UTIL_TIMER_Create_IgnoreAndReturn(UTIL_TIMER_OK);

	// Expect sequencer tasks registration
	for (int i = 0; i < LORA_APP_TASK_MAX_COUNT; i++) {
		UTIL_SEQ_RegTask_Expect(LORA_APP_TASK_BASE_ID << i,
		                        UTIL_SEQ_DEFAULT,
		                        *test_lora_app_process);
		UTIL_SEQ_RegTask_IgnoreArg_Flags();
	}

	lora_app_init();

	// Radio.Init called once with the correct RadioEvents_t fields
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.init_count);
	TEST_ASSERT_TRUE(radio_history.init_events_captured);
	TEST_ASSERT_EQUAL_PTR(*test_OnTxDone,
		radio_history.last_init_events.TxDone);
	TEST_ASSERT_EQUAL_PTR(*test_OnRxDone,
		radio_history.last_init_events.RxDone);
	TEST_ASSERT_EQUAL_PTR(*test_OnTxTimeout,
		radio_history.last_init_events.TxTimeout);
	TEST_ASSERT_EQUAL_PTR(*test_OnRxTimeout,
		radio_history.last_init_events.RxTimeout);
	TEST_ASSERT_EQUAL_PTR(*test_OnRxError,
		radio_history.last_init_events.RxError);

	// lora_recv called once, w/ continuous RX mode config
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.standby_count);
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.set_channel_count);
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.set_rx_config_count);
	TEST_ASSERT_EQUAL_UINT32(1,
		radio_history.set_max_payload_length_count);
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.rx_count);
	TEST_ASSERT_TRUE(radio_history.last_rx_continuous);

	// TX-specific counters unchanged
	TEST_ASSERT_EQUAL_UINT32(0, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(0, radio_history.set_tx_config_count);
	TEST_ASSERT_EQUAL_UINT32(0, radio_history.sleep_count);
}

/**
 * @brief Helper function for invoking lora_app_init with its associated
 * _Ignore's.
 */
static void drive_init(void) {
	UTIL_TIMER_Create_IgnoreAndReturn(UTIL_TIMER_OK);

	UTIL_SEQ_RegTask_Ignore();

	lora_app_init();
}

/**
 * @brief Helper function to build a packet with a toy source address.
 *
 * @param src_addr Address of the device from which the packet originates.
 *
 * @return Returns a packet with source address {@code src_addr}, valid SOF,
 * and otherwise garbage entries.
 */
static packet_t make_sentinel_packet(uint8_t src_addr) {
	packet_t p = {
		.sof = APP_SOF,
		.source_addr = src_addr,
		.dest_addr = 0xF3,
		.data_type = 0x12,
		.data = { 0x01, 0x02, 0x03, 0x04 }
	};
	return p;
}

/**
 * @brief Tests that lora_send claims, configures and drives the SubGHz radio TX
 * when the TX FIFO has at least one queued packet and tx_busy is false.
 */
void test_lora_send_on_non_empty_ring_and_non_busy_tx_claims_configures_and_drives_tx(void) {
	// Push one packet to TX FIFO
	packet_t p = make_sentinel_packet(0xBB);
	memcpy(&test_tx_pkts[0], &p, sizeof(packet_t));

	*test_tx_write_bufidx = 1;
	*test_tx_read_bufidx = 0;
	*test_tx_busy = false;

	uint32_t set_channels_before = radio_history.set_channel_count,
		set_tx_configs_before	 = radio_history.set_tx_config_count,
		set_max_payload_before   = radio_history.set_max_payload_length_count,
		standby_before           = radio_history.standby_count,
		sends_before			 = radio_history.send_count;

	test_lora_send();

	/* Verify calls to Radio.Standby, Radio.SetTxConfig, Radio.Send and
	 * Radio.SetMaxPayloadLength. */
	TEST_ASSERT_EQUAL_UINT32(standby_before + 1,
		radio_history.standby_count);
	TEST_ASSERT_EQUAL_UINT32(set_channels_before + 1,
		radio_history.set_channel_count);
	TEST_ASSERT_EQUAL_UINT32(set_tx_configs_before + 1,
		radio_history.set_tx_config_count);
	TEST_ASSERT_EQUAL_UINT32(set_max_payload_before + 1,
		radio_history.set_max_payload_length_count);
	TEST_ASSERT_EQUAL_UINT32(sends_before + 1,
		radio_history.send_count);
}

/**
 * @brief Tests lora_send early return path when TX FIFO is empty.
 */
void test_lora_send_on_empty_ring_returns_without_send(void) {
	*test_tx_write_bufidx = 0;
	*test_tx_read_bufidx = 0;

	uint32_t standby_before		= radio_history.standby_count,
		set_channels_before		= radio_history.set_channel_count,
		set_tx_configs_before	= radio_history.set_tx_config_count,
		set_max_payload_before	= radio_history.set_max_payload_length_count,
		sends_before			= radio_history.send_count;

	test_lora_send();

	// No radio driver API calls made
	TEST_ASSERT_EQUAL_UINT32(standby_before, radio_history.standby_count);
	TEST_ASSERT_EQUAL_UINT32(set_channels_before,
		radio_history.set_channel_count);
	TEST_ASSERT_EQUAL_UINT32(set_tx_configs_before,
		radio_history.set_tx_config_count);
	TEST_ASSERT_EQUAL_UINT32(set_max_payload_before,
		radio_history.set_max_payload_length_count);
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_FALSE(*test_tx_busy);
}

/**
 * @brief Tests lora_send no-op path when TX resource is already claimed.
 *
 * @note In debug builds, lora_send traps into an infinite loop when this
 * happens. Otherwise, this is an early return path.
 */
void test_lora_send_when_tx_busy_is_no_op(void) {
	// Push one packet to TX FIFO
	packet_t p = make_sentinel_packet(0xBB);
	memcpy(&test_tx_pkts[0], &p, sizeof(packet_t));

	*test_tx_write_bufidx = 1;
	*test_tx_read_bufidx = 0;
	*test_tx_busy = true;

	uint32_t standby_before		= radio_history.standby_count,
		set_channels_before		= radio_history.set_channel_count,
		set_tx_configs_before	= radio_history.set_tx_config_count,
		set_max_payload_before	= radio_history.set_max_payload_length_count,
		sends_before			= radio_history.send_count;

	test_lora_send();

	// No radio driver API calls made
	TEST_ASSERT_EQUAL_UINT32(standby_before, radio_history.standby_count);
	TEST_ASSERT_EQUAL_UINT32(set_channels_before,
		radio_history.set_channel_count);
	TEST_ASSERT_EQUAL_UINT32(set_tx_configs_before,
		radio_history.set_tx_config_count);
	TEST_ASSERT_EQUAL_UINT32(set_max_payload_before,
		radio_history.set_max_payload_length_count);
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_TRUE(*test_tx_busy);
}

/**
 * @brief Tests immediate TX start following a push to empty FIFO.
 *
 * @note Partially covers SRS-GW-03.
 */
void test_lora_schedule_send_on_empty_buffer_kicks_lora_send(void) {
	drive_init();

	packet_t p = make_sentinel_packet(0xAA);

	uint32_t sends_before    = radio_history.send_count;
	uint32_t standby_before  = radio_history.standby_count;

	// Empty ring buffer
	*test_tx_write_bufidx = 0;
	*test_tx_read_bufidx = 0;
	*test_tx_busy = false;

	// Sanity check
	TEST_ASSERT_EQUAL_INT(APP_STATUS_OK, lora_schedule_send(&p));

	// Expected behaviour: 1 lora_send call
	TEST_ASSERT_EQUAL_UINT32(standby_before + 1,
		radio_history.standby_count);
	TEST_ASSERT_EQUAL_UINT32(sends_before + 1, radio_history.send_count);

	// Verifying integrity of the forwarded packet
	TEST_ASSERT_EQUAL_UINT8(sizeof(packet_t), radio_history.last_size);
	TEST_ASSERT_EQUAL_MEMORY_ARRAY(&p, &radio_history.last_packet,
		1, sizeof(packet_t));

	// TX resource should remain claimed until OnTxDone or OnTxTimeout
	TEST_ASSERT_TRUE(*test_tx_busy);

	// First enqueue lands in slot 0 with retries reset.
	TEST_ASSERT_EQUAL_MEMORY_ARRAY(&p, &test_tx_pkts[0],
		1, sizeof(packet_t));
	TEST_ASSERT_EQUAL_UINT8(1, *test_tx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_tx_read_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, test_tx_retries[0]);
}

/**
 * @brief Tests TX FIFO enqueue below maximum threshold.
 */
void test_lora_schedule_send_fills_to_capacity_without_extra_sends(void) {
	drive_init();

	// Start with empty ring buffer
	*test_tx_write_bufidx = 0;
	*test_tx_read_bufidx = 0;
	*test_tx_busy = false;

	packet_t p[LORA_APP_TX_MAX_COUNT];

	// First enqueue kicks lora_send, claims tx_busy.
	p[0] = make_sentinel_packet(0xA0);
	(void)lora_schedule_send(&p[0]);

	uint32_t sends_after_first = radio_history.send_count;

	// Enqueue additional packets until FIFO is full
	for (int i = 1; i < LORA_APP_TX_MAX_COUNT; i++) {
		p[i] = make_sentinel_packet(0xA0 + i);
		TEST_ASSERT_EQUAL_INT(APP_STATUS_OK, lora_schedule_send(&p[i]));
	}

	// TX resource still claimed; no extra call to lora_send should've been made
	TEST_ASSERT_TRUE(*test_tx_busy);
	TEST_ASSERT_EQUAL_UINT32(sends_after_first, radio_history.send_count);

	// Check FIFO order correctness, and retries counters reset
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_TX_MAX_COUNT, *test_tx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_tx_read_bufidx);
	for (int i = 0; i < LORA_APP_TX_MAX_COUNT; i++) {
		TEST_ASSERT_EQUAL_MEMORY_ARRAY(&p[i], &test_tx_pkts[i],
			1, sizeof(packet_t));
		TEST_ASSERT_EQUAL_UINT8(0, test_tx_retries[i]);
	}
}

/**
 * @brief Tests TX packet drop beyond FIFO maximum threshold.
 */
void test_lora_schedule_send_on_full_buffer_returns_error(void) {
	drive_init();

	packet_t p[LORA_APP_TX_MAX_COUNT+1];

	// Start with empty buffer
	*test_tx_write_bufidx = 0;
	*test_tx_read_bufidx = 0;
	*test_tx_busy = false;

	// Fill FIFO
	for (uint8_t i = 0; i < LORA_APP_TX_MAX_COUNT; i++) {
		p[i] = make_sentinel_packet(i);
		(void)lora_schedule_send(&p[i]);
	}

	TEST_ASSERT_EQUAL_UINT8(LORA_APP_TX_MAX_COUNT, *test_tx_write_bufidx);
	uint32_t sends_after_fill = radio_history.send_count;

	// Overflowing packet must be dropped
	p[LORA_APP_TX_MAX_COUNT] = make_sentinel_packet(0xFF);
	TEST_ASSERT_EQUAL_INT(APP_STATUS_ERR_TX_BUFFER_FULL,
		lora_schedule_send(&p[LORA_APP_TX_MAX_COUNT]));

	// No further Radio.Send calls
	TEST_ASSERT_EQUAL_UINT32(sends_after_fill, radio_history.send_count);

	// Verify the ring buffer is unaffected
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_TX_MAX_COUNT, *test_tx_write_bufidx);
	for (int i = 0; i < LORA_APP_TX_MAX_COUNT; i++) {
		TEST_ASSERT_EQUAL_MEMORY_ARRAY(&p[i], &test_tx_pkts[i],
			1, sizeof(packet_t));
	}
}

/**
 * @brief Tests that OnTxDone clears tx_busy, enqueues TX_DONE into the state
 * ring and fires UTIL_SEQ_SetTask with the correct task ID.
 */
void test_OnTxDone_frees_tx_enqueues_TX_DONE_and_fires_task(void) {
	// Simulate ongoing TX
	*test_tx_busy = true;

	// Empty state ring buffer
	*test_state_write_bufidx = 0;
	*test_state_read_bufidx = 0;

	// Expect base task to be set
	UTIL_SEQ_SetTask_Expect(LORA_APP_TASK_BASE_ID, CFG_SEQ_Prio_0);
	UTIL_SEQ_SetTask_IgnoreArg_Task_Prio();

	test_OnTxDone();

	// Verify that the TX resource is now freed
	TEST_ASSERT_FALSE(*test_tx_busy);

	// Check that the state ring buffer now contains one entry: TX_DONE
	TEST_ASSERT_EQUAL_UINT8(1, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	/* enum ApplicationState promotes to int, and standard integer-promotion
	 * rules preserve values for our enumerator range (0..4), so the cast is
	 * value-safe across all compilers. */
	TEST_ASSERT_EQUAL_INT(TX_DONE, test_states[0]);
}

/**
 * @brief Tests that OnTxDone, on full state FIFO, frees TX resource but skips
 * task enqueue.
 */
void test_OnTxDone_on_full_state_buffer_frees_tx_but_skips_task_enqueue(void) {
	// Simulate ongoing TX
	*test_tx_busy = true;

	enum ApplicationState state_rbuf_before[LORA_APP_TASK_MAX_COUNT];

	// Fill state ring buffer
	for (int i = 0; i < LORA_APP_TX_MAX_COUNT; i++) {
		test_states[i] = i;
		state_rbuf_before[i] = i;
	}
	*test_state_write_bufidx = LORA_APP_TASK_MAX_COUNT;
	*test_state_read_bufidx  = 0;

	test_OnTxDone();

	// Verify that the TX resource is now freed
	TEST_ASSERT_FALSE(*test_tx_busy);

	// Verify that the state ring buffer is unchanged
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_TASK_MAX_COUNT, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	TEST_ASSERT_EQUAL_MEMORY_ARRAY(state_rbuf_before, test_states,
		sizeof(enum ApplicationState), LORA_APP_TX_MAX_COUNT);
}


/**
 * @brief Tests that OnRxDone, on a matching payload size (+ non-full state and
 * RX buffers), enqueues RX_DONE, copies payload into rx_pkts[], rssi into
 * rssis[] and snr into snrs[].
 */
void test_OnRxDone_on_matching_payload_size_enqueues_RX_DONE_and_copies_to_rx_rings(void) {
	// Build matching size packet
	packet_t p = make_sentinel_packet(0xAA);

	// Expect base task to be set
	UTIL_SEQ_SetTask_Expect(LORA_APP_TASK_BASE_ID, 0);
	UTIL_SEQ_SetTask_IgnoreArg_Task_Prio();

	// Simulate successful RX
	test_OnRxDone((uint8_t*)&p, sizeof(p), -57, 7);

	// Check that the state ring buffer now contains one entry: RX_DONE
	TEST_ASSERT_EQUAL_UINT8(1, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	/* enum ApplicationState promotes to int, and standard integer-promotion
	 * rules preserve values for our enumerator range (0..4), so the cast is
	 * value-safe across all compilers. */
	TEST_ASSERT_EQUAL_INT(RX_DONE, test_states[0]);

	// Check that the RX ring buffers now contain one entry each
	TEST_ASSERT_EQUAL_UINT8(1, *test_rx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_read_bufidx);

	TEST_ASSERT_EQUAL_MEMORY_ARRAY(&p, &test_rx_pkts[0],
		1, sizeof(packet_t));
	TEST_ASSERT_EQUAL_INT16(-57, test_rssis[0]);
	TEST_ASSERT_EQUAL_INT8(7, test_snrs[0]);
}

/**
 * @brief Tests that OnRxDone, on payload size mismatch (+ non-full state ring
 * buffer), enqueues RX_ERROR + RX_ERROR_SIZE_MISMATCH.
 */
void test_OnRxDone_on_size_mismatch_enqueues_RX_ERROR_SIZE_MISMATCH(void) {
	// Build mismatching size payload
	uint8_t buf[LORA_APP_PAYLOAD_LEN - 1] = {0};

	// Expect base task to be set
	UTIL_SEQ_SetTask_Expect(LORA_APP_TASK_BASE_ID, 0);
	UTIL_SEQ_SetTask_IgnoreArg_Task_Prio();

	// Simulate successful RX
	test_OnRxDone(buf, sizeof(buf), -57, 7);

	/* Check that the state ring buffer now contains one entry: RX_ERROR, and
	 * the RX error ring buffer: RX_ERROR_SIZE_MISMATCH. */
	TEST_ASSERT_EQUAL_UINT8(1, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	/* enum ApplicationState and enum RxErrorType promote to int, and standard
	 * integer-promotion rules preserve values for our enumerator ranges
	 * (0..4 and 0..2), so the cast is value-safe across all compilers. */
	TEST_ASSERT_EQUAL_INT(RX_ERROR, test_states[0]);
	TEST_ASSERT_EQUAL_INT(RX_ERROR_SIZE_MISMATCH, test_rx_error[0]);

	// Check that the RX ring remains unchanged
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_read_bufidx);
}

/**
 * @brief Tests that OnRxDone, on a matching size payload but full RX ring
 * buffer (+ non-full state ring buffer), enqueues RX_ERROR +
 * RX_ERROR_RX_BUFFER_FULL.
 */
void test_OnRxDone_on_full_rx_ring_enqueues_RX_ERROR_RX_BUFFER_FULL(void) {
	packet_t p[LORA_APP_RX_MAX_COUNT+1];
	for (int i = 0; i < LORA_APP_RX_MAX_COUNT; i++) {
		p[i] = make_sentinel_packet(0xA0 + i);
		test_rx_pkts[i] = p[i];
	}

	// Simulate full ring buffer
	*test_rx_write_bufidx = LORA_APP_RX_MAX_COUNT;
	*test_rx_read_bufidx = 0;

	// Build matching size packet
	p[LORA_APP_RX_MAX_COUNT] = make_sentinel_packet(0xFF);

	// Expect base task to be set
	UTIL_SEQ_SetTask_Expect(LORA_APP_TASK_BASE_ID, 0);
	UTIL_SEQ_SetTask_IgnoreArg_Task_Prio();

	// Simulate successful RX
	test_OnRxDone((uint8_t*)&p[LORA_APP_RX_MAX_COUNT], sizeof(packet_t), -57, 7);

	/* Check that the state ring buffer now contains one entry: RX_ERROR, and
	 * the RX error ring buffer: RX_ERROR_RX_BUFFER_FULL. */
	TEST_ASSERT_EQUAL_UINT8(1, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	/* enum ApplicationState and enum RxErrorType promote to int, and standard
	 * integer-promotion rules preserve values for our enumerator ranges
	 * (0..4 and 0..2), so the cast is value-safe across all compilers. */
	TEST_ASSERT_EQUAL_INT(RX_ERROR, test_states[0]);
	TEST_ASSERT_EQUAL_INT(RX_ERROR_RX_BUFFER_FULL, test_rx_error[0]);

	// Check that the RX ring buffer is untouched
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_RX_MAX_COUNT, *test_rx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_read_bufidx);
	TEST_ASSERT_EQUAL_MEMORY_ARRAY(p, test_rx_pkts,
		LORA_APP_PAYLOAD_LEN, LORA_APP_RX_MAX_COUNT);
}

/**
 * @brief Tests OnRxDone early return path on state-ring exhaustion.
 */
void test_OnRxDone_on_full_state_ring_early_returns(void) {
	enum ApplicationState state_rbuf_before[LORA_APP_TASK_MAX_COUNT];

	// Fill state ring buffer
	for (int i = 0; i < LORA_APP_TASK_MAX_COUNT; i++) {
		test_states[i] = i;
		state_rbuf_before[i] = i;
	}
	*test_state_write_bufidx = LORA_APP_TASK_MAX_COUNT;
	*test_state_read_bufidx  = 0;

	// Build matching size packet
	packet_t p = make_sentinel_packet(0xAA);
	// Build mismatching size packet
	uint8_t buf[LORA_APP_PAYLOAD_LEN-1] = {0};

	// Simulate both matching and mismatching RXs
	test_OnRxDone((uint8_t*)&p, sizeof(p), -57, 7);
	test_OnRxDone(buf, sizeof(buf), -57, 7);

	// State ring buffer untouched
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_TASK_MAX_COUNT, *test_state_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_state_read_bufidx);
	TEST_ASSERT_EQUAL_MEMORY_ARRAY(state_rbuf_before, test_states,
		sizeof(enum ApplicationState), LORA_APP_TASK_MAX_COUNT);

	// RX ring buffer still empty
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_write_bufidx);
	TEST_ASSERT_EQUAL_UINT8(0, *test_rx_read_bufidx);
}


/* Test Runner --------------------------------------------------------------*/
int main(void) {
	UNITY_BEGIN();

	RUN_TEST(test_application_configuration_matches_srs);

	RUN_TEST(test_lora_app_init_registers_tasks_inits_radio_and_starts_rx);

	RUN_TEST(test_lora_send_on_non_empty_ring_and_non_busy_tx_claims_configures_and_drives_tx);
	RUN_TEST(test_lora_send_on_empty_ring_returns_without_send);
	RUN_TEST(test_lora_send_when_tx_busy_is_no_op);

	RUN_TEST(test_lora_schedule_send_on_empty_buffer_kicks_lora_send);
	RUN_TEST(test_lora_schedule_send_fills_to_capacity_without_extra_sends);
	RUN_TEST(test_lora_schedule_send_on_full_buffer_returns_error);

	RUN_TEST(test_OnTxDone_frees_tx_enqueues_TX_DONE_and_fires_task);
	RUN_TEST(test_OnTxDone_on_full_state_buffer_frees_tx_but_skips_task_enqueue);

	RUN_TEST(test_OnRxDone_on_matching_payload_size_enqueues_RX_DONE_and_copies_to_rx_rings);
	RUN_TEST(test_OnRxDone_on_size_mismatch_enqueues_RX_ERROR_SIZE_MISMATCH);
	RUN_TEST(test_OnRxDone_on_full_rx_ring_enqueues_RX_ERROR_RX_BUFFER_FULL);
	RUN_TEST(test_OnRxDone_on_full_state_ring_early_returns);

	return UNITY_END();
}
