#ifndef __APP_CONFIG_H_
#define __APP_CONFIG_H_

/* Gateway application configuration -----------------------------------------*/
/* ---- Application version ---- */
#define APP_VERSION_MAJOR				1
#define APP_VERSION_MINOR				0

/* ---- MAC Configuration ---- */
#define APP_SOF							0xA5
#define APP_MY_ADDR						0x00


/* LoRa Application Configuration --------------------------------------------*/
/* ---- SubGHz configuration ---- */
#define LORA_APP_FREQ					433000000 /* Hz */
#define LORA_APP_TX_POWER				14        /* dBm */
#define LORA_APP_BW						0         /* [0: 125 kHz, 1: 250 kHz, 2: 500 kHz, 3: Reserved] */
#define LORA_APP_SF						7         /* [SF7..SF12] */
#define LORA_APP_CODINGRATE				1         /* [1: 4/5, 2: 4/6, 3: 4/7, 4: 4/8] */
#define LORA_APP_PREAMBLE_LENGTH		8         /* Same for Tx and Rx */
#define LORA_APP_PAYLOAD_LEN			sizeof(packet_t)

/* ---- Application configuration ---- */
#define LORA_APP_MY_ADDR				APP_MY_ADDR

#define LORA_APP_TASK_BASE_ID			(1 << 0)
#define LORA_APP_TASK_MAX_COUNT			16

#define LORA_APP_RX_MAX_COUNT			6
#define LORA_APP_RX_TIMEOUT				2000
#define LORA_APP_TX_PERIOD				5000

#define LORA_APP_TX_MAX_COUNT			6
#define LORA_APP_TX_TIMEOUT				500
#define LORA_APP_TX_MAX_RETRIES			3

/* ---- Debug LEDs ---- */
#define LORA_APP_LED_BLINK_DURATION		100
#define LORA_APP_TX_LED_GPIO_PORT		GPIOB
#define LORA_APP_TX_LED_GPIO_PIN		GPIO_PIN_9
#define LORA_APP_RX_LED_GPIO_PORT		GPIOB
#define LORA_APP_RX_LED_GPIO_PIN		GPIO_PIN_11


/* MAC Layer Definition ------------------------------------------------------*/
typedef struct {
	uint8_t sof,
			source_addr,
			dest_addr,
			data_type,
			data[4];
} packet_t;


/* Application status --------------------------------------------------------*/
typedef enum {
	APP_STATUS_OK,
	APP_STATUS_ERR_TX_EXHAUSTED
} AppStatus_t ;


#endif