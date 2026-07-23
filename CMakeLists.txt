cmake_minimum_required(VERSION 3.22)
project(LoRa_App VERSION 1.0)

option(BUILD_TESTING "Enable unit tests" OFF) # Default is OFF

if (BUILD_TESTING)
    enable_testing()
    add_subdirectory(tests)
else ()
    add_subdirectory(gateway)
    add_subdirectory(endpoint1)
endif ()