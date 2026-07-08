#ifndef __LORA_APP_H_
#define __LORA_APP_H_

#ifdef __cplusplus
extern "C" {
#endif

#include "app/app_config.h"

/**
  * @brief Init SubGHz application
  */
void lora_app_init(void);

/**
 * @brief Push packet pointed to by pkt to SubGHz TX FIFO
 * @param pkt pointer to packet_t object
 * @return APP_STATUS_OK on success, positive integer error code on error
 */
AppStatus_t lora_schedule_send(packet_t* pkt);

#ifdef __cplusplus
}
#endif

#endif /* __LORA_APP_H_ */
