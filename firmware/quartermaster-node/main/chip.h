#pragma once

#include "sdkconfig.h"
#include "soc/soc_caps.h"
#include "soc/uart_pins.h"

#define CHIP_UART_TX U0TXD_GPIO_NUM
#define CHIP_UART_RX U0RXD_GPIO_NUM

#define CHIP_PINS(lo, hi) ((((1ULL << ((hi) - (lo) + 1)) - 1)) << (lo))

#if CONFIG_IDF_TARGET_ESP32C5
#define CHIP_NAME "c5"
#define CHIP_BUTTON 28
#define CHIP_LED_PINS (CHIP_PINS(0, 10) | CHIP_PINS(23, 28))
#elif CONFIG_IDF_TARGET_ESP32C3
#define CHIP_NAME "c3"
#define CHIP_BUTTON 9
#define CHIP_LED_PINS CHIP_PINS(0, 10)
#elif CONFIG_IDF_TARGET_ESP32C6
#define CHIP_NAME "c6"
#define CHIP_BUTTON 9
#define CHIP_LED_PINS (CHIP_PINS(0, 11) | CHIP_PINS(14, 15) | CHIP_PINS(18, 23))
#elif CONFIG_IDF_TARGET_ESP32S3
#define CHIP_NAME "s3"
#define CHIP_BUTTON 0
#define CHIP_LED_PINS (CHIP_PINS(0, 18) | CHIP_PINS(21, 21) | CHIP_PINS(38, 42) | CHIP_PINS(45, 48))
#else
#error "Quartermaster Node runs on the ESP32-C5, ESP32-C3, ESP32-C6 and ESP32-S3"
#endif

#if SOC_WIFI_SUPPORT_5G
#define CHIP_CAPS "wifi24+wifi5"
#else
#define CHIP_CAPS "wifi24"
#endif
