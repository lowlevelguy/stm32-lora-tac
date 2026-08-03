#ifndef __MOCK_APP_STATE_H_
#define __MOCK_APP_STATE_H_

/*
 * Test-only declaration of the gateway SUT's internal state-machine
 * enumerators.
 *
 * The SUT (gateway/core/src/app/lora_app.c) declares these enums as
 * file-scope (no `static`, but no public header either); the test TU
 * cannot reach them via the production header chain. To allow the test
 * to assert on `state_ring` slot values via the SUT's
 * `extern volatile enum ApplicationState *test_states` accessor
 * pointer, the test TU must see a COMPLETE enum type (forward-declared
 * enums are incomplete and cannot be dereferenced).
 *
 * The enumerator list and ordering here MUST mirror the SUT's
 * file-scope definition exactly. Any divergence would silently break
 * numeric-equality assertions across TUs (the linker does not enforce
 * enum body consistency across TUs, only within a single TU).
 *
 * @note This header is included ONLY by the test TU. The production
 *       SUT keeps its own file-scope enum definition; cross-TU type
 *       compatibility is preserved because both definitions share the
 *       same tag name and equivalent enumerator bodies (C99 permits
 *       this for enums).
 */

/**
 * @brief State-machine slots written by ISR callbacks and consumed by
 *        lora_app_process().
 *
 * Numeric values (in source order): TX_DONE=0, TX_TIMEOUT=1, RX_DONE=2,
 * RX_ERROR=3, UNEXPECTED=4.
 */
enum ApplicationState {
	TX_DONE, TX_TIMEOUT,
	RX_DONE, RX_ERROR,
	UNEXPECTED
};

/**
 * @brief RX-error sub-codes written into rx_error[] alongside the
 *        RX_ERROR state slot.
 *
 * Numeric values: RX_ERROR_EXTERNAL=0, RX_ERROR_SIZE_MISMATCH=1,
 * RX_ERROR_RX_BUFFER_FULL=2.
 */
enum RxErrorType {
	RX_ERROR_EXTERNAL,
	RX_ERROR_SIZE_MISMATCH,
	RX_ERROR_RX_BUFFER_FULL
};

#endif /* __MOCK_APP_STATE_H_ */
