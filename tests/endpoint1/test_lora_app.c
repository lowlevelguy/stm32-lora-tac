#include <unity.h>
#include <string.h>

#include "app/lora_app.h"
#include "mock_stm32_hal_gpio.h"
#include "mock_stm32_seq.h"
#include "mock_stm32_timer.h"
#include "radio.h"

/* Stub Sensor ---------------------------------------------------------------*/
uint8_t btn_press_count = 0;


/* Test-only SUT accessors ---------------------------------------------------*/
extern void (*test_OnTxDone)(void);
extern void (*test_OnRxDone)(uint8_t*, uint16_t, int16_t, int8_t);
extern void (*test_OnTxTimeout)(void);
extern void (*test_OnRxTimeout)(void);
extern void (*test_OnRxError)(void);
extern void (*test_OnTxTimer)(void*);
extern void (*test_OnTxLedTimer)(void*);
extern void (*test_OnRxLedTimer)(void*);
extern void (*test_OnAckLedTimer)(void*);
extern void (*test_lora_app_process)(void);
extern UTIL_TIMER_Object_t *test_tx_timer;
extern UTIL_TIMER_Object_t *test_tx_led_timer;
extern UTIL_TIMER_Object_t *test_rx_led_timer;
extern UTIL_TIMER_Object_t *test_ack_led_timer;
extern void test_reset_lora_app_state(void);


/* Radio History -------------------------------------------------------------*/
static struct {
	packet_t     last_packet;
	uint16_t     last_size;
	uint32_t     last_rx_timeout;
	RadioEvents_t last_init_events;
	bool         init_events_captured;
	uint32_t     send_count;
	uint32_t     rx_count;
	uint32_t     sleep_count;
	uint32_t	 standby_count;
	uint32_t     init_count;
	uint32_t     set_channel_count;
	uint32_t     set_rx_config_count;
	uint32_t     set_tx_config_count;
	uint32_t     set_max_payload_length_count;
} radio_history;


/* Stub Radio Driver ---------------------------------------------------------*/
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
                                  uint32_t bandwidthAfc, uint16_t preambleLen,
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

static void fake_Radio_Sleep(void) {
	radio_history.sleep_count++;
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

const struct Radio_s Radio = {
	.Init                = fake_Radio_Init,
	.SetChannel          = fake_Radio_SetChannel,
	.SetRxConfig         = fake_Radio_SetRxConfig,
	.SetTxConfig         = fake_Radio_SetTxConfig,
	.Send                = fake_Radio_Send,
	.Sleep               = fake_Radio_Sleep,
	.Standby			 = fake_Radio_Standby,
	.Rx                  = fake_Radio_Rx,
	.SetMaxPayloadLength = fake_Radio_SetMaxPayloadLength,
};

/**
 * @brief Reset test state: radio history, stub sensor reading, and SUT state.
 */
static void reset_test_state(void) {
	memset(&radio_history, 0, sizeof(radio_history));
	btn_press_count = 0;
	test_reset_lora_app_state();
}

/* Unity Harness -------------------------------------------------------------*/
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
 * @note Partially covers SRS-ED-01:06 testing.
 */
static void test_application_configuration_matches_srs(void) {
	/* ---- SRS-ED-01 ---- */
	TEST_ASSERT_EQUAL_UINT32(5000, LORA_APP_TX_PERIOD);

	/* ---- SRS-ED-02,04,05 ---- */
	TEST_ASSERT_EQUAL_UINT8(0xA5, LORA_APP_SOF);

	/* ---- SRS-ED-03 ---- */
	TEST_ASSERT_EQUAL_UINT32(2000, LORA_APP_RX_TIMEOUT);

	/* ---- SRS-ED-06 ---- */
	TEST_ASSERT_EQUAL_UINT32(433000000, LORA_APP_FREQ);
	TEST_ASSERT_EQUAL_UINT32(0, LORA_APP_BW);			// 125kHz BW maps to 0
	TEST_ASSERT_EQUAL_UINT32(7, LORA_APP_SF);
	TEST_ASSERT_EQUAL_UINT32(1, LORA_APP_CODINGRATE);	// 4/5 coding rate maps to 1
	TEST_ASSERT_EQUAL_UINT8(14, LORA_APP_TX_POWER);
}

/**
 * @brief Test app initialization.
 *
 * @note Partially covers SRS-ED-01 testing.
 */
static void test_lora_app_init_creates_timers_starts_tx_and_inits_radio(void) {
	/* As it currently is, lora_app_init makes no call to SetTask. However, if
	 * it was ever to do so, that wouldn't break program logic; the SRS doesn't
	 * specify if a telemetry TX should occur right after boot. */
	UTIL_SEQ_SetTask_Ignore();

	// The exact task flags are not vital for correctness.
	UTIL_SEQ_RegTask_Expect(LORA_APP_TASK_ID, 0, *test_lora_app_process);
	UTIL_SEQ_RegTask_IgnoreArg_Flags();

	/* Expect TX timer init and start, but ignore the debug LED timers, as they
	 * are not vital for correctness. */
	UTIL_TIMER_Create_ExpectAndReturn(
		test_tx_timer, LORA_APP_TX_PERIOD,
		UTIL_TIMER_PERIODIC, *test_OnTxTimer, NULL,
		UTIL_TIMER_OK);
	UTIL_TIMER_Create_IgnoreAndReturn(UTIL_TIMER_OK);

	UTIL_TIMER_Start_ExpectAndReturn(test_tx_timer, UTIL_TIMER_OK);

	lora_app_init();

	/* Radio.Init was called once with a non-empty RadioEvents_t. */
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.init_count);
	TEST_ASSERT_TRUE(radio_history.init_events_captured);
	TEST_ASSERT_NOT_NULL(radio_history.last_init_events.TxDone);
	TEST_ASSERT_NOT_NULL(radio_history.last_init_events.RxDone);
	TEST_ASSERT_NOT_NULL(radio_history.last_init_events.TxTimeout);
	TEST_ASSERT_NOT_NULL(radio_history.last_init_events.RxTimeout);
	TEST_ASSERT_NOT_NULL(radio_history.last_init_events.RxError);
}

/**
 * @brief Helper function for initiating telemetry TX.
 */
static void drive_init_and_telemetry_tx(void) {
	UTIL_SEQ_SetTask_Ignore();
	UTIL_SEQ_RegTask_Ignore();
	UTIL_TIMER_Create_IgnoreAndReturn(UTIL_TIMER_OK);
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	lora_app_init();

	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	test_OnTxTimer(NULL);
	test_lora_app_process();
}

/**
 * @brief Tests telemetry TX packet format.
 *
 * @note Covers SRS-ED-02 testing.
 */
static void test_telemetry_tx_sends_correct_packet_fields(void) {
	btn_press_count = 5;

	drive_init_and_telemetry_tx();

	// Packet validation
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_SOF,
	                        radio_history.last_packet.sof);
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_MY_ADDR,
	                        radio_history.last_packet.source_addr);
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_GATEWAY_ADDR,
	                        radio_history.last_packet.dest_addr);
	TEST_ASSERT_EQUAL_UINT8(PACKET_DATA_TYPE_TELEMETRY,
	                        radio_history.last_packet.data_type);
	TEST_ASSERT_EQUAL_UINT8(
		TELEMETRY_TYPE_BUTTON_PRESS_COUNT,
		radio_history.last_packet.data[0]);
	TEST_ASSERT_EQUAL_UINT8(5, radio_history.last_packet.data[1]);
	TEST_ASSERT_EQUAL_UINT8(0, radio_history.last_packet.data[2]);
	TEST_ASSERT_EQUAL_UINT8(0, radio_history.last_packet.data[3]);

	// Validate the fact that by the time a TX runs, the radio methods
	// SetChannel, SetTxConfig and SetMaxPayloadLength have all been called.
	TEST_ASSERT_GREATER_THAN_UINT32(0,
		radio_history.set_channel_count);
	TEST_ASSERT_GREATER_THAN_UINT32(0,
		radio_history.set_tx_config_count);
	TEST_ASSERT_GREATER_THAN_UINT32(0,
		radio_history.set_max_payload_length_count);
}

/**
 * @brief Tests the transition to RX as soon as TX_DONE happens.
 *
 * @note Partially covers SRS-ED-03 testing.
 */
static void test_telemetry_tx_done_transitions_to_rx_listen(void) {
	drive_init_and_telemetry_tx();

	// Validate the fact that the time of RX start is properly recorded, to
	// allow for the "re-listen" mechanism to function properly.
	// The return value is unimportant garbage.
	HAL_GetTick_ExpectAndReturn(0);

	test_OnTxDone();
	test_lora_app_process();

	/* ---- SRS-ED-03 ---- */
	// Assert that RX is configured with the correct timeout
	TEST_ASSERT_EQUAL_UINT32(1, radio_history.rx_count);
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_RX_TIMEOUT,
		radio_history.last_rx_timeout);
}

/**
 * @brief Tests the TX retry mechanism below the maximum threshold.
 */
static void test_tx_timeout_retries_send_below_max(void) {
	drive_init_and_telemetry_tx();

	// Snapshot count after the first send
	uint32_t sends_before = radio_history.send_count;

	// Expect TX retry
	test_OnTxTimeout();
	test_lora_app_process();
	TEST_ASSERT_EQUAL_UINT32(sends_before + 1, radio_history.send_count);
}

/**
 * @brief Tests the TX retry mechanism beyond the maximum threshold.
 */
static void test_tx_timeout_after_max_retries_does_not_send(void) {
	drive_init_and_telemetry_tx();	// send=1, retries=0, state=TX

	// Retry until max retries reached
	test_OnTxTimeout();				// state=TX_TIMEOUT
	for (int i = 0; i < LORA_APP_TX_MAX_RETRIES; i++) {
		test_lora_app_process();	// send++, retries++
	}								// send=MAX+1, retries=MAX

	// The packet should be dropped
	test_lora_app_process();
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_TX_MAX_RETRIES+1, radio_history.send_count);
}

/**
 * @brief Helper function for initiating command RX.
 *
 * @param fake_tick_at_listen stub value for RX start time
 */
static void drive_into_rx_listen(uint32_t fake_tick_at_listen) {
	drive_init_and_telemetry_tx();
	HAL_GetTick_ExpectAndReturn(fake_tick_at_listen);
	test_OnTxDone();
	test_lora_app_process();
}

/**
 * @brief Helper function for building a downlink command packet.
 *
 * @param actuator_id ID of the actuator to drive
 * @param opcode integer value indicating the operation to execute on the
 * actuator
 *
 * @return Return the thus built packet.
 */
static packet_t make_gateway_command(uint8_t actuator_id, uint8_t opcode) {
	packet_t p = {
		.sof         = LORA_APP_SOF,
		.source_addr = LORA_APP_GATEWAY_ADDR,
		.dest_addr   = LORA_APP_MY_ADDR,
		.data_type   = PACKET_DATA_TYPE_COMMAND,
	};
	p.data[0] = actuator_id;
	p.data[1] = opcode;
	p.data[2] = 0;
	p.data[3] = 0;
	return p;
}

/**
 * @brief Tests command execution on RX, as well as subsequent ACK TX.
 *
 * @note Partially covers SRS-ED-04,05 testing.
 */
static void test_rx_done_valid_command_executes_actuator_and_sends_ack(void) {
	drive_into_rx_listen(0);

	// Build a "red LED turn on" command packet
	packet_t cmd = make_gateway_command(
		ACTUATOR_ID_LED_RED, LED_RED_COMMAND_TURN_ON);

	/* Ignore debug LED.
	 * Instead of using WritePin_Ignore, which would introduce a conflict
	 * with the later WritePin_Expect call, we manually ignore each of the
	 * function's arguments as a workaround. */
	HAL_GPIO_WritePin_Expect(NULL, 0, GPIO_PIN_SET);
	HAL_GPIO_WritePin_IgnoreArg_GPIOx();
	HAL_GPIO_WritePin_IgnoreArg_GPIO_Pin();
	HAL_GPIO_WritePin_IgnoreArg_PinState();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	/* ---- SRS-ED-04 ---- */
	// The red LED should turn on, on command execution
	HAL_GPIO_WritePin_Expect(LED3_GPIO_Port, LED3_Pin, GPIO_PIN_SET);

	// drive_into_rx_listen already issued 1 Radio.Send + 1 Radio.Rx
	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	// Expected behaviour on reception: one ACK TX, no further RX
	TEST_ASSERT_EQUAL_UINT32(sends_before + 1,
							 radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before, radio_history.rx_count);

	/* ---- SRS-ED-05 ---- */
	// Validate ACK packet format
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_SOF,
	                        radio_history.last_packet.sof);
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_MY_ADDR,
	                        radio_history.last_packet.source_addr);
	TEST_ASSERT_EQUAL_UINT8(LORA_APP_GATEWAY_ADDR,
	                        radio_history.last_packet.dest_addr);
	TEST_ASSERT_EQUAL_UINT8(PACKET_DATA_TYPE_ACK,
	                        radio_history.last_packet.data_type);
	TEST_ASSERT_EQUAL_UINT8(ACTUATOR_ID_LED_RED,
	                        radio_history.last_packet.data[0]);
	TEST_ASSERT_EQUAL_UINT8(ACK_STATUS_OK,
	                        radio_history.last_packet.data[1]);
}

/**
 * @brief Tests packet drop on mismatching destination address RX, and the
 * subsequent "re-listen" mechanism.
 */
static void test_rx_done_wrong_destination_reenters_rx(void) {
	drive_into_rx_listen(0);

	// Build downlink packet with mismatched destination address
	packet_t cmd = make_gateway_command(
		ACTUATOR_ID_LED_RED, LED_RED_COMMAND_TURN_ON);
	cmd.dest_addr = LORA_APP_MY_ADDR + 1;

	// Ignore debug LED
	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	// Stub elapsed time since RX start: 300ms
	HAL_GetTick_ExpectAndReturn(300);

	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	// Expected behaviour: no ACK TX, re-enters RX with 300ms less in timeout
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before + 1, radio_history.rx_count);
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_RX_TIMEOUT - 300,
	                         radio_history.last_rx_timeout);
}

/**
 * @brief Tests packet drop on invalid SOF RX, and the subsequent "re-listen"
 * mechanism.
 */
static void test_rx_done_invalid_sof_reenters_rx(void) {
	drive_into_rx_listen(0);

	// Build downlink packet with invalid SOF
	packet_t cmd = make_gateway_command(
		ACTUATOR_ID_LED_RED, LED_RED_COMMAND_TURN_ON);
	cmd.sof = LORA_APP_SOF + 1;

	// Ignore debug LED
	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	// Stub elapsed time since RX start: 100ms
	HAL_GetTick_ExpectAndReturn(100);

	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	// Expected behaviour: no ACK TX, re-enters RX with 100ms less in timeout
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before + 1, radio_history.rx_count);
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_RX_TIMEOUT - 100,
	                         radio_history.last_rx_timeout);
}

/**
 * @brief Tests packet drop on unknown actuator ID RX, and the subsequent
 * "re-listen" mechanism.
 */
static void test_rx_done_unknown_actuator_reenters_rx(void) {
	drive_into_rx_listen(0);

	// Build downlink packet with unknown actuator ID
	packet_t cmd = make_gateway_command(ACTUATOR_ID_COUNT, 0);

	// Ignore debug LED
	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	// Stub elapsed time since RX start: 500ms
	HAL_GetTick_ExpectAndReturn(500);

	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	// Expected behaviour: no ACK TX, re-enters RX with 500ms less in timeout
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before + 1, radio_history.rx_count);
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_RX_TIMEOUT - 500,
	                         radio_history.last_rx_timeout);
}

/**
 * @brief Tests packet drop on invalid command RX, and the subsequent
 * "re-listen" mechanism.
 */
static void test_rx_done_invalid_command_opcode_reenters_rx(void) {
	drive_into_rx_listen(0);

	// Build downlink packet with invalid opcode
	packet_t cmd = make_gateway_command(
		ACTUATOR_ID_LED_RED, 0xFF);

	// Ignore debug LED
	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	// Stub elapsed time since RX start: 200ms
	HAL_GetTick_ExpectAndReturn(200);

	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	// Expected behaviour: no ACK TX, re-enters RX with 200ms less in timeout
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before + 1, radio_history.rx_count);
	TEST_ASSERT_EQUAL_UINT32(LORA_APP_RX_TIMEOUT - 200,
	                         radio_history.last_rx_timeout);
}

/**
 * @brief Tests RX timeout behaviour.
 */
static void test_rx_timeout_logs_and_ends_listen_cycle(void) {
	drive_into_rx_listen(0);

	uint32_t sends_before = radio_history.send_count;
	uint32_t rx_before    = radio_history.rx_count;

	// Stub RX timeout
	test_OnRxTimeout();
	test_lora_app_process();

	// Expected behaviour: no ACK TX, no RX
	TEST_ASSERT_EQUAL_UINT32(sends_before, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_before, radio_history.rx_count);
}

/**
 * @brief Tests post-ACK behaviour.
 */
static void test_ack_done_is_no_op_after_successful_command_tx(void) {
	drive_into_rx_listen(0);

	packet_t cmd = make_gateway_command(
		ACTUATOR_ID_LED_RED, LED_RED_COMMAND_TURN_ON);

	// Ignore debug and actuator LEDs
	HAL_GPIO_WritePin_Ignore();
	UTIL_TIMER_Start_IgnoreAndReturn(UTIL_TIMER_OK);

	// Stub successful RX, with garbage RSSI and SNR values
	test_OnRxDone((uint8_t*)&cmd, sizeof(cmd), -55, 7);
	test_lora_app_process();

	uint32_t sends_after_ack = radio_history.send_count;
	uint32_t rx_after_ack    = radio_history.rx_count;

	// Stub successful ACK TX
	test_OnTxDone();
	test_lora_app_process();

	// Expected behaviour: no further TX or RX
	TEST_ASSERT_EQUAL_UINT32(sends_after_ack, radio_history.send_count);
	TEST_ASSERT_EQUAL_UINT32(rx_after_ack, radio_history.rx_count);
}

/* Test Runner ---------------------------------------------------------------*/
int main(void) {
	UNITY_BEGIN();

	RUN_TEST(test_application_configuration_matches_srs);

	/* Group 1: init sanity */
	RUN_TEST(test_lora_app_init_creates_timers_starts_tx_and_inits_radio);

	/* Group 2: telemetry TX (SRS-ED-01 / 02) and TX-timeout retry */
	RUN_TEST(test_telemetry_tx_sends_correct_packet_fields);
	RUN_TEST(test_telemetry_tx_done_transitions_to_rx_listen);
	RUN_TEST(test_tx_timeout_retries_send_below_max);
	RUN_TEST(test_tx_timeout_after_max_retries_does_not_send);

	/* Group 3: command RX (SRS-ED-03 / 04) */
	RUN_TEST(test_rx_done_valid_command_executes_actuator_and_sends_ack);
	RUN_TEST(test_rx_done_wrong_destination_reenters_rx);
	RUN_TEST(test_rx_done_invalid_sof_reenters_rx);
	RUN_TEST(test_rx_done_unknown_actuator_reenters_rx);
	RUN_TEST(test_rx_done_invalid_command_opcode_reenters_rx);
	RUN_TEST(test_rx_timeout_logs_and_ends_listen_cycle);

	/* Group 4: ACK TX (SRS-ED-05) */
	RUN_TEST(test_ack_done_is_no_op_after_successful_command_tx);

	return UNITY_END();
}
