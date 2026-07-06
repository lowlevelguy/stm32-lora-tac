#include "app/lora_app.h"

#include "platform.h"
#include "sys_app.h"
#include "radio.h"
#include "stm32_seq.h"
#include "stm32_timer.h"


extern uint8_t btn_press_count;

static RadioEvents_t RadioEvents;

enum ApplicationState state;
enum RxErrorType rx_error = RX_ERROR_EXTERNAL;

uint8_t timestamp = 0, tx_retries = 0;
packet_t rx_pkt = {0}, tx_pkt = { .sof = LORA_APP_SOF };
uint32_t rx_start_time;

static UTIL_TIMER_Object_t tx_timer,
	tx_led_timer, rx_led_timer, ack_led_timer;

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
  * @param  rssi received frame RSSI
  * @param  snr received frame SNR
  */
static void OnRxDone(uint8_t* payload, uint16_t size, int16_t rssi, int8_t snr) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnRxDone\n\r");

	if (size == LORA_APP_PAYLOAD_LEN) {
		memcpy(&rx_pkt, payload, size);
		state = RX_DONE;
	} else {
		rx_error = RX_ERROR_SIZE_MISMATCH;
		state = RX_ERROR;
	}

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
	APP_LOG(TS_ON, "OnRxTimeout\n\r");

	state = RX_TIMEOUT;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
  * @brief Function executed on Radio Rx Error event
  */
static void OnRxError(void) {
	Radio.Sleep();
	APP_LOG(TS_ON, "OnRxError\n\r");

	state = RX_ERROR;
	rx_error = RX_ERROR_EXTERNAL;
	UTIL_SEQ_SetTask(LORA_APP_TASK_ID, CFG_SEQ_Prio_0);
}

/**
 * @brief Attempt to have the SubGHz module transmit the packet pointed to by pkt.
 * @param pkt pointer to packet_t object
 */
static void lora_send(packet_t* pkt) {
	Radio.Sleep();

	// SubGHz TX Configuration
	Radio.SetChannel(LORA_APP_FREQ);
	Radio.SetTxConfig(MODEM_LORA, LORA_APP_TX_POWER, 0,
		LORA_APP_BW, LORA_APP_SF, LORA_APP_CODINGRATE,
		LORA_APP_PREAMBLE_LENGTH, RADIO_LORA_PACKET_FIXED_LENGTH, RADIO_LORA_CRC_ON,
		false, 0, RADIO_LORA_IQ_NORMAL, LORA_APP_TX_TIMEOUT);
	Radio.SetMaxPayloadLength(MODEM_LORA, LORA_APP_PAYLOAD_LEN);

	Radio.Send((uint8_t*)pkt, sizeof(packet_t));
}

/**
 * @brief Make the SubGHz module enter single-mode RX w/ timeout and attempt
 * to capture a packet.
 * @param timeout maximum duration to keep listening for in milliseconds
 */
static void lora_recv(uint32_t timeout) {
	Radio.Sleep();

	// SubGHz RX Configuration
	Radio.SetChannel(LORA_APP_FREQ);
	Radio.SetRxConfig(MODEM_LORA, LORA_APP_BW, LORA_APP_SF, LORA_APP_CODINGRATE,
		0, LORA_APP_PREAMBLE_LENGTH, 0,
		RADIO_LORA_PACKET_FIXED_LENGTH, LORA_APP_PAYLOAD_LEN,
		RADIO_LORA_CRC_ON, false, 0, RADIO_LORA_IQ_NORMAL, false);
	Radio.SetMaxPayloadLength(MODEM_LORA, LORA_APP_PAYLOAD_LEN);

	Radio.Rx(timeout);
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
 * @brief Function executed on led3_timer expiry
 * @param p unused pointer parameter
 */
static void OnAckLedTimer(void* p) {
	UNUSED(p);
	HAL_GPIO_WritePin(LORA_APP_ACK_LED_GPIO_PORT, LORA_APP_ACK_LED_GPIO_PIN,
	GPIO_PIN_RESET);
}

/**
 * @brief Main Application Process
 */
static void lora_app_process(void) {
	uint32_t elapsed_time;

	Radio.Sleep();

	switch (state) {
	case TX:
		// Make TX LED blink for debugging purposes
		HAL_GPIO_WritePin(LORA_APP_TX_LED_GPIO_PORT, LORA_APP_TX_LED_GPIO_PIN, GPIO_PIN_SET);
		UTIL_TIMER_Start(&tx_led_timer);

		tx_pkt.source_addr = LORA_APP_MY_ADDR;
		tx_pkt.dest_addr = LORA_APP_GATEWAY_ADDR;
		tx_pkt.data_type = PACKET_DATA_TYPE_TELEMETRY;

		/*
		 * Telemetry data format:
		 * data[0]: telemetry type
		 * data[1]: timestamp
		 * data[2]: telemetry value
		 * data[3]: unused; gateway will populate it with received SNR
		 */
		tx_pkt.data[0] = TELEMETRY_TYPE_BUTTON_PRESS_COUNT;
		tx_pkt.data[1] = timestamp++;
		tx_pkt.data[2] = btn_press_count;
		tx_pkt.data[3] = 0;

		APP_LOG(TS_ON, "Committing telemetry: src_addr(%u), dest_addr(%u), "
		        "telemetry_type(%u), timestamp(%u), telemetry_value(%u).\n\r",
		        tx_pkt.source_addr, tx_pkt.dest_addr,
		        tx_pkt.data[0], tx_pkt.data[1], tx_pkt.data[2]);

		lora_send(&tx_pkt);
		break;

	case TX_DONE:
		// Listen for any commands
		APP_LOG(TS_ON, "Listening for any commands for 2 seconds...\n\r");
		rx_start_time = HAL_GetTick();
		lora_recv(LORA_APP_RX_TIMEOUT);
		break;

	case TX_TIMEOUT:
		if (tx_retries < LORA_APP_TX_MAX_RETRIES) {
			APP_LOG(TS_ON, "")
			lora_send(&tx_pkt);
			tx_retries++;
		}
		break;

	case RX_DONE:
		// Make RX LED blink for debugging purposes
		HAL_GPIO_WritePin(LORA_APP_RX_LED_GPIO_PORT, LORA_APP_RX_LED_GPIO_PIN, GPIO_PIN_SET);
		UTIL_TIMER_Start(&rx_led_timer);

		// Check packet validity
		if (rx_pkt.sof == LORA_APP_SOF &&
			rx_pkt.source_addr == LORA_APP_GATEWAY_ADDR &&
			rx_pkt.dest_addr == LORA_APP_MY_ADDR &&
			rx_pkt.data_type == PACKET_DATA_TYPE_COMMAND) {

			/*
			 * Command data format:
			 * data[0]: actuator ID
			 * data[1-3]: params for the associated command function
			 */
			int i = 0;
			for (i = 0; i < ACTUATOR_ID_COUNT; i++) {
				if (rx_pkt.data[0] == actuators[i].actuator_id) {
					uint8_t err_code = actuators[i].command(&rx_pkt.data[1]);

					// If command was valid; transmit ACK
					if (err_code != COMMAND_STATUS_UNKNOWN) {
						if (err_code == COMMAND_STATUS_OK) {
							APP_LOG(TS_ON, "Actuator command "
								"(id=%u, params=%#02x %#02x %#02x) executed.\n\r",
								rx_pkt.data[0], rx_pkt.data[1], rx_pkt.data[2],
								rx_pkt.data[3]);
						} else if (err_code == COMMAND_STATUS_ERROR) {
							APP_LOG(TS_ON, "Error occured on command execution.\n\r");
						}

						tx_pkt.source_addr = LORA_APP_MY_ADDR;
						tx_pkt.dest_addr = LORA_APP_GATEWAY_ADDR;
						tx_pkt.data_type = PACKET_DATA_TYPE_ACK;

						/*
						 * ACK data format:
						 * data[0]: actuator ID
						 * data[1]: 0x00 = OK
						 * data[2-3]: unused
						 */
						tx_pkt.data[0] = rx_pkt.data[0];
						tx_pkt.data[1] = 0;
						tx_pkt.data[2] = 0;
						tx_pkt.data[3] = 0;

						lora_send(&tx_pkt);

					} else {
						// Invalid command; go back into RX
						APP_LOG(TS_ON, "Unrecognized actuator command "
							"(id=%u, params = %#02x %#02x %#02x).\n\r",
							rx_pkt.data[0], rx_pkt.data[1], rx_pkt.data[2],
							rx_pkt.data[3]);

						elapsed_time = HAL_GetTick() - rx_start_time;
						if (elapsed_time < LORA_APP_RX_TIMEOUT) {
							APP_LOG(TS_ON, "Re-entering RX mode...\n\r");
							lora_recv(LORA_APP_RX_TIMEOUT - elapsed_time);
						} else {
							APP_LOG(TS_ON, "No commands received.\n\r");
						}
					}
				}
			}

			// Unknown actuator; go back into RX mode again
			if (i == ACTUATOR_ID_COUNT) {
				APP_LOG(TS_ON, "Unknown actuator (id=%u).\n\r", rx_pkt.data[0]);

				elapsed_time = HAL_GetTick() - rx_start_time;
				if (elapsed_time < LORA_APP_RX_TIMEOUT) {
					APP_LOG(TS_ON, "Re-entering RX mode...\n\r");
					lora_recv(LORA_APP_RX_TIMEOUT - elapsed_time);
				} else {
					APP_LOG(TS_ON, "No commands received.\n\r");
				}
			}

		} else {
			elapsed_time = HAL_GetTick() - rx_start_time;
			if (elapsed_time < LORA_APP_RX_TIMEOUT) {
				APP_LOG(TS_ON, "Re-entering RX mode...\n\r");
				lora_recv(LORA_APP_RX_TIMEOUT - elapsed_time);
			} else {
				APP_LOG(TS_ON, "No commands received.\n\r");
			}
		}
		break;

	case RX_TIMEOUT:
		APP_LOG(TS_ON, "No commands received.\n\r");
		break;

	case RX_ERROR:
		switch (rx_error) {
		case RX_ERROR_SIZE_MISMATCH:
			APP_LOG(TS_ON, "Received packet of unexpected size.\n\r");
			break;
		case RX_ERROR_EXTERNAL:
		default:
			APP_LOG(TS_ON, "Erroneous reception.\n\r");
			break;
		};

		elapsed_time = HAL_GetTick() - rx_start_time;
		if (elapsed_time < LORA_APP_RX_TIMEOUT) {
			APP_LOG(TS_ON, "Re-entering RX mode...\n\r");
			lora_recv(LORA_APP_RX_TIMEOUT - elapsed_time);
		} else {
			APP_LOG(TS_ON, "No commands received.\n\r");
		}

		break;

	case ACK_DONE:
	case ACK_TIMEOUT:
		break;
	case UNEXPECTED:
	default:
		APP_LOG(TS_ON, "Unexpected FSM state reached!\n\r");
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
	UTIL_TIMER_Create(&tx_led_timer, APP_LORA_LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
	                  OnTxLedTimer, NULL);
	UTIL_TIMER_Create(&rx_led_timer, APP_LORA_LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
	                  OnRxLedTimer, NULL);
	UTIL_TIMER_Create(&ack_led_timer, APP_LORA_LED_BLINK_DURATION, UTIL_TIMER_ONESHOT,
	                  OnAckLedTimer, NULL);

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
