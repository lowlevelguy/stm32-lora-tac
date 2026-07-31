#include <unity.h>
#include <string.h>

#include "app/lora_app.h"

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

extern void test_reset_lora_app_state(void);


/* Radio History -------------------------------------------------------------*/
static struct {
	packet_t      last_packet;
	uint16_t      last_size;
	uint32_t      last_rx_timeout;
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
	UNUSED(iqInverted);   UNUSED(rxContinuous);
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
	radio_history.last_rx_timeout = timeout;
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


/* Smoke Tests --------------------------------------------------------------*/
/**
 * @brief Sanity check that APP_SOF matches the SRS value.
 *
 * Verifies the dummy include chain resolves and the test binary links.
 * The 13 test groups from the handoff are added incrementally in
 * subsequent iterations; this lone smoke test is the green baseline.
 */
void test_sof_matches_srs(void) {
	TEST_ASSERT_EQUAL_UINT8(0xA5, APP_SOF);
}


/* Test Runner --------------------------------------------------------------*/
int main(void) {
	UNITY_BEGIN();

	RUN_TEST(test_sof_matches_srs);

	return UNITY_END();
}
