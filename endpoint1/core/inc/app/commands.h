#ifndef __COMMANDS_H_
#define __COMMANDS_H_

#include <stdint.h>

enum ActuatorID {
	ACTUATOR_ID_LED_RED,

	ACTUATOR_ID_COUNT
};

enum CommandOpcode {
	COMMAND_OPCODE_TURN_OFF,
	COMMAND_OPCODE_TURN_ON,
};

enum CommandStatus {
	COMMAND_STATUS_OK,
	COMMAND_STATUS_ERROR,
	COMMAND_STATUS_UNKNOWN
};

typedef struct {
	uint8_t actuator_id;
	uint8_t (*command)(void*);
} actuator_t;

extern actuator_t actuators[ACTUATOR_ID_COUNT];

#endif /* __COMMANDS_H_ */