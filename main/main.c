#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "student_impl.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_system.h"
#include "esp_timer.h"

#define LED_PIN    GPIO_NUM_26   // matches diagram.json
#define BTN_PIN    GPIO_NUM_4    // pushbutton to GND, use internal pull-up

void app_main(void) {
    // LED as output
    gpio_config_t io_led = {
        .pin_bit_mask = 1ULL << LED_PIN,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&io_led);

    // Button as input with pull-up (reads 1 when released, 0 when pressed)
    gpio_config_t io_btn = {
        .pin_bit_mask = 1ULL << BTN_PIN,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&io_btn);

    printf("READY\n");

    bool last_stable = false;
    bool led_state = false; // mirror LED pin

    uint64_t next_ms_mark = 0;
    int release_count = 0;

    for (;;) {
        // Time print
        uint64_t now_us = (uint64_t)esp_timer_get_time();
        uint64_t now_ms = now_us / 1000ULL;
        while (now_ms >= next_ms_mark) {
            // print exact millisecond mark
            printf("%llu\n", (unsigned long long)next_ms_mark);
            next_ms_mark += 100ULL;
        }

        // button/LED logic
        // Read raw (active low): pressed => 0
        bool raw_pressed = (gpio_get_level(BTN_PIN) == 0);

        // STUDENT function decides stable press via debounce
        bool stable = debounce(raw_pressed);

        // Update LED and log LED events on edges
        bool prev_led = led_state;
        led_state = stable;
        gpio_set_level(LED_PIN, led_state ? 1 : 0);
        if (led_state != prev_led) {
            printf("EVENT: LED %s\n", led_state ? "On" : "Off");
        }

        // Log button press/release events on edges
        if (stable != last_stable) {
            printf("EVENT: Button %s\n", stable ? "Press" : "Release");
            if (!stable) { // release
                release_count++;
                if (release_count == 2) {
                    printf("DONE\n");
                    fflush(stdout);
                    vTaskDelay(pdMS_TO_TICKS(100)); // give time to flush
                    vTaskDelete(NULL);
                    break; // stop loop
                }
            }
            last_stable = stable;
        }

        vTaskDelay(pdMS_TO_TICKS(5)); // 5ms poll
    }
}
