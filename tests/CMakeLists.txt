cmake_minimum_required(VERSION 3.22)

include(FetchContent)

# Download CMock (w/ Unity included) as a dependency
FetchContent_Declare(
	cmock
	GIT_REPOSITORY https://github.com/throwtheswitch/cmock.git
	GIT_TAG v2.7.0
)
FetchContent_MakeAvailable(cmock)

set(CMOCK_DIR ${cmock_SOURCE_DIR})
set(UNITY_DIR ${CMOCK_DIR}/vendor/unity)

# Build Unity as a static library
add_library(unity STATIC
	${UNITY_DIR}/src/unity.c
)
target_include_directories(unity PUBLIC
	${UNITY_DIR}/src
)

# Build CMock as a static library
add_library(cmock_lib STATIC
	${CMOCK_DIR}/src/cmock.c
)
target_include_directories(cmock_lib PUBLIC
	${CMOCK_DIR}/src
)
target_link_libraries(cmock_lib PUBLIC unity)


# CMock's mock generator is a Ruby script, so Ruby must be installed.
find_program(RUBY_EXECUTABLE ruby REQUIRED)
function(add_cmock_test)
	# ---- Parse the function arguments ----
	cmake_parse_arguments(
		ARG                              # Prefix for parsed variables
		""                               # No boolean flags
		"NAME"                           # Single-value args: NAME
		"SOURCES;MOCKS;INCLUDES;COMPILE_DEFS"  # Multi-value args
		${ARGN}                          # The actual arguments passed to the function
	)
	# After parsing, we can access:
	#   ARG_NAME         = the test name (e.g. "test_commands")
	#   ARG_SOURCES      = list of .c files to compile
	#   ARG_MOCKS        = list of headers to generate mocks from
	#   ARG_INCLUDES     = list of directories to add to the include path
	#   ARG_COMPILE_DEFS = list of -D defines private to this test target

	# ---- Generate mocks ----
	set(MOCK_OUTPUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/mocks/${ARG_NAME})
	file(MAKE_DIRECTORY ${MOCK_OUTPUT_DIR})

	set(MOCK_SOURCES "")
	foreach (MOCK_HEADER ${ARG_MOCKS})
		get_filename_component(MOCK_BASE ${MOCK_HEADER} NAME_WE)

		set(MOCK_C ${MOCK_OUTPUT_DIR}/mock_${MOCK_BASE}.c)
		set(MOCK_H ${MOCK_OUTPUT_DIR}/mock_${MOCK_BASE}.h)

		add_custom_command(
			OUTPUT ${MOCK_C} ${MOCK_H}
			COMMAND ${RUBY_EXECUTABLE}
			${CMOCK_DIR}/lib/cmock.rb
			-o${CMAKE_CURRENT_SOURCE_DIR}/cmock_config.yml
			${MOCK_HEADER}
			DEPENDS ${MOCK_HEADER}
			WORKING_DIRECTORY ${MOCK_OUTPUT_DIR}
			COMMENT "CMock: Generating mock for ${MOCK_HEADER}"
		)

		list(APPEND MOCK_SOURCES ${MOCK_C})
	endforeach ()

	# ---- Create the test executable ----
	add_executable(${ARG_NAME}
		${ARG_SOURCES}
		${MOCK_SOURCES}
	)

	target_include_directories(${ARG_NAME} PRIVATE
		${MOCK_OUTPUT_DIR}
		${ARG_INCLUDES}
	)

	target_link_libraries(${ARG_NAME} PRIVATE
		unity
		cmock_lib
	)

	# ---- Optional per-target compile definitions (e.g. BUILD_TESTING) ----
	# Used to expose #ifdef-guarded SUT accessor blocks to a single test
	# target without leaking the define into the production firmware build.
	if (ARG_COMPILE_DEFS)
		target_compile_definitions(${ARG_NAME} PRIVATE ${ARG_COMPILE_DEFS})
	endif ()

	# ---- Register with CTest ----
	add_test(NAME ${ARG_NAME} COMMAND ${ARG_NAME})
endfunction()


# Adding unit tests
# Test 1 - endpoint1::commands.c
add_cmock_test(
	NAME endpoint1_test_commands
	SOURCES
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/test_commands.c
    	${CMAKE_SOURCE_DIR}/endpoint1/core/src/app/commands.c
	MOCKS
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers/stm32_hal_gpio.h
	INCLUDES
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers
    	${CMAKE_SOURCE_DIR}/endpoint1/core/inc
)

# Test 2 - endpoint1::lora_app.c
add_cmock_test(
	NAME endpoint1_test_lora_app
	SOURCES
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/test_lora_app.c
    	${CMAKE_SOURCE_DIR}/endpoint1/core/src/app/lora_app.c
    	${CMAKE_SOURCE_DIR}/endpoint1/core/src/app/commands.c
	MOCKS
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers/stm32_hal_gpio.h
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers/stm32_seq.h
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers/stm32_timer.h
	INCLUDES
    	${CMAKE_CURRENT_SOURCE_DIR}/endpoint1/dummy_headers
    	${CMAKE_SOURCE_DIR}/endpoint1/core/inc
	COMPILE_DEFS
    	BUILD_TESTING
)

# Test 3 - gateway::lora_app.c
add_cmock_test(
	NAME gateway_test_lora_app
	SOURCES
    	${CMAKE_CURRENT_SOURCE_DIR}/gateway/test_lora_app.c
    	${CMAKE_SOURCE_DIR}/gateway/core/src/app/lora_app.c
	MOCKS
    	${CMAKE_CURRENT_SOURCE_DIR}/gateway/dummy_headers/stm32_hal_gpio.h
    	${CMAKE_CURRENT_SOURCE_DIR}/gateway/dummy_headers/stm32_seq.h
    	${CMAKE_CURRENT_SOURCE_DIR}/gateway/dummy_headers/stm32_timer.h
	INCLUDES
    	${CMAKE_CURRENT_SOURCE_DIR}/gateway/dummy_headers
    	${CMAKE_SOURCE_DIR}/gateway/core/inc
	COMPILE_DEFS
    	BUILD_TESTING
)
