#include <unity.h>

#include "main.h"
#include "app/commands.h"
#include "mock_stm32_hal_gpio.h"

void setUp(void) {}

void tearDown(void) {
	mock_stm32_hal_gpio_Verify();
	mock_stm32_hal_gpio_Destroy();
}

static void test_smoke_linker_and_unity_run(void) {
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_OK, COMMAND_STATUS_OK);
}

static void test_actuators_table_initialized(void) {
	// Verifying that the actuators count value is properly configured as
	// greater than that of the LED (ACTUATOR_ID_LED_RED < ACTUATOR_ID_COUNT)
	TEST_ASSERT_GREATER_THAN_UINT8(ACTUATOR_ID_LED_RED, ACTUATOR_ID_COUNT);

	// Verifying that the LED's ActuatorID is 0x00
	TEST_ASSERT_EQUAL_UINT8(ACTUATOR_ID_LED_RED, 0);

	// Verifying that the actuator table fields are properly configured
	TEST_ASSERT_EQUAL_UINT8(ACTUATOR_ID_LED_RED, actuators[0].actuator_id);
	TEST_ASSERT_NOT_NULL(actuators[0].command);
}

static void test_led_red_command_turn_off_calls_gpio_reset_and_returns_ok(void) {
	uint8_t params[3] = { LED_RED_COMMAND_TURN_OFF, 0, 0 };

	HAL_GPIO_WritePin_Expect(LED3_GPIO_Port, LED3_Pin, GPIO_PIN_RESET);
	uint8_t status = actuators[ACTUATOR_ID_LED_RED].command(params);
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_OK, status);
}

static void test_led_red_command_turn_on_calls_gpio_set_and_returns_ok(void) {
	uint8_t params[3] = { LED_RED_COMMAND_TURN_ON, 0, 0 };

	HAL_GPIO_WritePin_Expect(LED3_GPIO_Port, LED3_Pin, GPIO_PIN_SET);
	uint8_t status = actuators[ACTUATOR_ID_LED_RED].command(params);
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_OK, status);
}

static void test_led_red_command_unknown_opcode_does_not_touch_gpio_and_returns_unknown(void) {
	uint8_t params[3] = { 0x02, 0, 0 };

	// When :fail_on_unexpected_calls is set to true, the test fails on any
	// call to HAL_GPIO_WritePin with no prior _Expect or _Ignore call.
	uint8_t status = actuators[ACTUATOR_ID_LED_RED].command(params);
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_UNKNOWN, status);
}

static void test_led_red_command_ignores_trailing_param_bytes(void) {
	uint8_t params[3] = { COMMAND_OPCODE_TURN_OFF, 0xAA, 0xBB };

	HAL_GPIO_WritePin_Expect(LED3_GPIO_Port, LED3_Pin, GPIO_PIN_RESET);
	uint8_t status = actuators[ACTUATOR_ID_LED_RED].command(params);
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_OK, status);
}

int main(void) {
	UNITY_BEGIN();

	RUN_TEST(test_smoke_linker_and_unity_run);
	RUN_TEST(test_actuators_table_initialized);
	RUN_TEST(test_led_red_command_turn_off_calls_gpio_reset_and_returns_ok);
	RUN_TEST(test_led_red_command_turn_on_calls_gpio_set_and_returns_ok);
	RUN_TEST(test_led_red_command_unknown_opcode_does_not_touch_gpio_and_returns_unknown);
	RUN_TEST(test_led_red_command_ignores_trailing_param_bytes);

	return UNITY_END();
}