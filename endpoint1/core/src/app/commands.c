#include "app/commands.h"

#include "main.h"

/* Private types -------------------------------------------------------------*/
enum LedRedCommand {
	LED_RED_COMMAND_TURN_OFF,
	LED_RED_COMMAND_TURN_ON,
};

/* Private functions ---------------------------------------------------------*/
/**
 * @brief Red LED commanding function
 * @param p buffer of parameters, expected of static size 3.
 * @return COMMAND_STATUS_OK on success, COMMAND_STATUS_ERROR on functional
 * error, COMMAND_STATUS_UNKNOWN on invalid command.
 */
static uint8_t led_red_command(void* p) {
	uint8_t* params = p;

	/* ---- SRS-ED-04 ---- */
	/*
	 * The red LED actuator supports two commands: "turn off", and "turn on".
	 * For the "turn off" command to take place, params[0] has to equal 0.
	 * For the "turn on" command to take place, params[0] has to equal 1.
	 * Any other values are ignored. Any values inside params[1] and params[2]
	 * are ignored.
	 */
	switch (params[0]) {
	case LED_RED_COMMAND_TURN_OFF:
		HAL_GPIO_WritePin(LED3_GPIO_PORT, LED3_PIN, GPIO_PIN_RESET);
		return COMMAND_STATUS_OK;

	case LED_RED_COMMAND_TURN_ON:
		HAL_GPIO_WritePin(LED3_GPIO_PORT, LED3_PIN, GPIO_PIN_SET);
		return COMMAND_STATUS_OK;

	default:
		return COMMAND_STATUS_UNKNOWN;
	};
}

/* Exported variables --------------------------------------------------------*/
actuator_t actuators[ACTUATOR_ID_COUNT] = {
	{
		.actuator_id = ACTUATOR_ID_LED_RED,
		.command = led_red_command
	}
};