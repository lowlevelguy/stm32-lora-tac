#ifndef __UART_APP_H_
#define __UART_APP_H_

#ifdef __cplusplus
extern "C" {
#endif

#include "app/app_config.h"

/**
 * @brief Init UART application
 */
void uart_app_init(void);

/**
 * @brief Transmit packet pointed to by pkt over UART
 * @param pkt pointer to packet_t object to send
 * @return APP_STATUS_OK on success, positive integer error code on error
 */
AppStatus_t uart_schedule_send(packet_t* pkt);

#ifdef __cplusplus
}
#endif

#endif