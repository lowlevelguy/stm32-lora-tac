#include <unity.h>
#include "app/commands.h"

void setUp(void) {}
void tearDown(void) {}

static void test_smoke_linker_and_unity_run(void) {
	TEST_ASSERT_EQUAL_UINT8(COMMAND_STATUS_OK, COMMAND_STATUS_OK);
}

int main(void) {
	UNITY_BEGIN();
	RUN_TEST(test_smoke_linker_and_unity_run);
	return UNITY_END();
}