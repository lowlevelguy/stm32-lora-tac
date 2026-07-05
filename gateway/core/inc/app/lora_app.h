#ifndef __LORA_APP_H_
#define __LORA_APP_H_

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ---- Application information ---- */
#define LORA_APP_VERSION_MAJOR			1
#define LORA_APP_VERSION_MINOR			0

/* ---- SubGHz configuration ---- */
#define LORA_APP_FREQ					433000000 /* Hz */
#define LORA_APP_TX_POWER				14        /* dBm */
#define LORA_APP_BW						0         /* [0: 125 kHz, 1: 250 kHz, 2: 500 kHz, 3: Reserved] */
#define LORA_APP_SF						7         /* [SF7..SF12] */
#define LORA_APP_CODINGRATE				1         /* [1: 4/5, 2: 4/6, 3: 4/7, 4: 4/8] */
#define LORA_APP_PREAMBLE_LENGTH		8         /* Same for Tx and Rx */
#define LORA_APP_PAYLOAD_LEN			sizeof(packet_t)

/* ---- Application configuration ---- */
#define LORA_APP_TASK_BASE_ID			(1 << 0)
#define LORA_APP_TASK_MAX_COUNT			16

#define LORA_APP_RX_MAX_COUNT			6
#define LORA_APP_RX_TIMEOUT				2000
#define LORA_APP_TX_PERIOD				5000

#define LORA_APP_TX_MAX_COUNT			6
#define LORA_APP_TX_TIMEOUT				500
#define LORA_APP_TX_MAX_RETRIES			3
#define APP_LORA_LED_BLINK_DURATION		100

/* ---- Packet ----*/
#define LORA_APP_SOF					0xA5
#define LORA_APP_MY_ADDR				0x00

/* ---- Debug LED Mapping ---- */
#define LORA_APP_TX_LED_GPIO_PORT		GPIOB
#define LORA_APP_TX_LED_GPIO_PIN		GPIO_PIN_9
#define LORA_APP_RX_LED_GPIO_PORT		GPIOB
#define LORA_APP_RX_LED_GPIO_PIN		GPIO_PIN_11

/* Types ----------------------------------------------------------------------*/
/* ---- Packet related types ---- */
enum PacketDataType {
	PACKET_DATA_TYPE_TELEMETRY = 0x01,
	PACKET_DATA_TYPE_COMMAND,
	PACKET_DATA_TYPE_ACK,

	PACKET_DATA_TYPE_RESERVED = 0xFF,
};

enum TelemetryType {
	TELEMETRY_TYPE_BUTTON_PRESS_COUNT
};

typedef struct {
	uint8_t sof,
			source_addr,
			dest_addr,
			data_type,
			data[4];
} packet_t;

/* ---- Error codes ---- */
enum {
	ERR_TX_EXHAUSTED = 1
};

/* API functions --------------------------------------------------------------*/
/**
  * @brief  Init Subghz Application
  */
void lora_app_init(void);

/**
 * @brief Transmit packet pointed to by pkt over LoRa.
 * @param pkt pointer to packet_t object
 * @return 0 on success, positive integer error code on error
 */
uint8_t lora_app_send(packet_t* pkt);

#ifdef __cplusplus
}
#endif

#endif /* __LORA_APP_H_ */
