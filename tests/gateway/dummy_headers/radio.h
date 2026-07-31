#ifndef __MOCK_RADIO_H_
#define __MOCK_RADIO_H_

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include <stdint.h>

/* Public typedef ------------------------------------------------------------*/

/**
 * @brief Lifted from radio_def.h:52-58
 */
typedef enum {
    RADIO_STATUS_OK,
    RADIO_STATUS_UNSUPPORTED_FEATURE,
    RADIO_STATUS_UNKNOWN_VALUE,
    RADIO_STATUS_ERROR,
} radio_status_t;

/**
 * @brief Lifted from radio_def.h:63-71.
 */
typedef enum {
    MODEM_FSK = 0,
    MODEM_LORA,
    MODEM_MSK,
    MODEM_BPSK,
    MODEM_SIGFOX_TX,
    MODEM_SIGFOX_RX,
} RadioModems_t;

/**
 * @brief Lifted from radio_ex.h:84-90
 */
typedef enum {
    RADIO_LORA_PACKET_VARIABLE_LENGTH = 0x00,
    RADIO_LORA_PACKET_FIXED_LENGTH    = 0x01,
    RADIO_LORA_PACKET_EXPLICIT        = RADIO_LORA_PACKET_VARIABLE_LENGTH,
    RADIO_LORA_PACKET_IMPLICIT        = RADIO_LORA_PACKET_FIXED_LENGTH,
} RADIO_LoRaPacketLengthsMode_t;

/**
 * @brief Lifted from radio_ex.h:95-99
 */
typedef enum {
    RADIO_LORA_CRC_OFF = 0x00,
    RADIO_LORA_CRC_ON  = 0x01,
} RADIO_LoRaCrcModes_t;

/**
 * @brief Lifted from radio_ex.h:104-108
 */
typedef enum {
    RADIO_LORA_IQ_NORMAL   = 0x00,
    RADIO_LORA_IQ_INVERTED = 0x01,
} RADIO_LoRaIQModes_t;

/**
 * @brief Lifted from radio_def.h:76-117.
 *
 * @note The FhssChangeChannel and CadDone slots are unused by the SUT and
 * exist to preserve the production struct layout so SUT-init code compiles
 * unchanged.
 */
typedef struct {
    void (*TxDone)(void);
    void (*TxTimeout)(void);
    void (*RxDone)(uint8_t *payload, uint16_t size, int16_t rssi, int8_t LoraSnr_FskCfo);
    void (*RxTimeout)(void);
    void (*RxError)(void);
    void (*FhssChangeChannel)(uint8_t currentChannel);
    void (*CadDone)(bool channelActivityDetected);
} RadioEvents_t;

/**
 * @brief Trimmed from radio.h:96-460 to only preserve the function-pointer
 * members the SUT invokes.
 *
 * @note This is intentionally NOT mockable by CMock: the API is exposed via
 * function pointers inside a struct, which CMock cannot synthesize. The test
 * translation unit instead defines its own driver instance, populating each
 * member with a stub function that records or returns whatever the test needs.
 *
 * @note If a future SUT change introduces a new `Radio.X()` call, the
 * compiler will emit an "unknown member 'X' of 'struct Radio_s'"
 * diagnostic -- a signal to extend this dummy deliberately.
 */
struct Radio_s {
    void           (*Init)(RadioEvents_t *events);
    void           (*SetChannel)(uint32_t freq);
    void           (*SetRxConfig)(RadioModems_t modem, uint32_t bandwidth,
                                  uint32_t datarate, uint8_t coderate,
                                  uint32_t bandwidthAfc, uint16_t preambleLen,
                                  uint16_t symbTimeout, bool fixLen,
                                  uint8_t payloadLen, bool crcOn,
                                  bool freqHopOn, uint8_t hopPeriod,
                                  bool iqInverted, bool rxContinuous);
    void           (*SetTxConfig)(RadioModems_t modem, int8_t power,
                                  uint32_t fdev, uint32_t bandwidth,
                                  uint32_t datarate, uint8_t coderate,
                                  uint16_t preambleLen, bool fixLen,
                                  bool crcOn, bool freqHopOn,
                                  uint8_t hopPeriod, bool iqInverted,
                                  uint32_t timeout);
    radio_status_t (*Send)(uint8_t *buffer, uint8_t size);
    void           (*Standby)(void);
    void           (*Rx)(uint32_t timeout);
    void           (*SetMaxPayloadLength)(RadioModems_t modem, uint8_t max);
};

/**
 * @brief Stub driver implementation, to be manually defined in test_lora_app.c.
 */
extern const struct Radio_s Radio;

#ifdef __cplusplus
}
#endif

#endif /* __MOCK_RADIO_H_ */
