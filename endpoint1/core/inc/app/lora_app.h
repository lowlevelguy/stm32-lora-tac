#ifndef __LORA_APP_H_
#define __LORA_APP_H_

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "main.h"
#include "app/commands.h"

/* ---- Application information ---- */
#define LORA_APP_VERSION_MAJOR			1
#define LORA_APP_VERSION_MINOR			0

/* ---- SubGHz configuration (SRS-ED-06) ---- */
// Only the low-band Nucleo variant is available
#define LORA_APP_FREQ					433000000 /* Hz */
#define LORA_APP_TX_POWER				14        /* dBm */
// Symbol time about 1ms, packet time about 24ms
#define LORA_APP_BW						0         /* [0: 125 kHz, 1: 250 kHz, 2: 500 kHz, 3: Reserved] */
#define LORA_APP_SF						7         /* [SF7..SF12] */
#define LORA_APP_CODINGRATE				1         /* [1: 4/5, 2: 4/6, 3: 4/7, 4: 4/8] */
#define LORA_APP_PREAMBLE_LENGTH		8         /* Same for Tx and Rx */
// Fixed-length payload mode
#define LORA_APP_PAYLOAD_LEN			sizeof(packet_t)

/* ---- Application configuration ---- */
#define LORA_APP_TASK_ID				(1 << 0)
#define LORA_APP_RX_TIMEOUT				2000
#define LORA_APP_TX_PERIOD				5000
#define LORA_APP_TX_TIMEOUT				500
#define LORA_APP_TX_MAX_RETRIES			3
#define APP_LORA_LED_BLINK_DURATION		100

/* ---- Packet ----*/
#define LORA_APP_SOF					0xA5
#define LORA_APP_GATEWAY_ADDR			0x00
#define LORA_APP_MY_ADDR				0x01

/* ---- Debug LED Mapping ---- */
#define LORA_APP_TX_LED_GPIO_PORT		LED1_GPIO_Port
#define LORA_APP_TX_LED_GPIO_PIN		LED1_PIN
#define LORA_APP_RX_LED_GPIO_PORT		LED2_GPIO_Port
#define LORA_APP_RX_LED_GPIO_PIN		LED2_PIN
#define LORA_APP_ACK_LED_GPIO_PORT		LORA_APP_TX_LED_GPIO_PORT
#define LORA_APP_ACK_LED_GPIO_PIN		LORA_APP_TX_LED_GPIO_PIN

/* Types ----------------------------------------------------------------------*/
/* ---- Application state types ---- */
enum ApplicationState {
	TX, TX_DONE, TX_TIMEOUT,
	RX_DONE, RX_TIMEOUT, RX_ERROR,
	ACK_DONE, ACK_TIMEOUT,
	UNEXPECTED
};

enum RxErrorType {
	RX_ERROR_EXTERNAL,
	RX_ERROR_SIZE_MISMATCH,
};

/* ---- Packet related types ---- */
enum PacketDataType {
	PACKET_DATA_TYPE_TELEMETRY = 0x01,
	PACKET_DATA_TYPE_COMMAND,
	PACKET_DATA_TYPE_ACK,

	PACKET_DATA_TYPE_RESERVED = 0xFF,
};

typedef struct {
	uint8_t sof,
			source_addr,
			dest_addr,
			data_type,
			data[4];
} packet_t;

enum TelemetryType {
	TELEMETRY_TYPE_BUTTON_PRESS_COUNT
};

enum AckStatus {
	ACK_STATUS_OK,
	ACK_STATUS_ERROR
};

/* API functions --------------------------------------------------------------*/
/**
  * @brief  Init Subghz Application
  */
void lora_app_init(void);

#ifdef __cplusplus
}
#endif

#endif /* __LORA_APP_H_ */
