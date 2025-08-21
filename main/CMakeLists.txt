idf_component_register(SRCS "main.c" "student_impl.c"
                        PRIV_REQUIRES spi_flash esp_driver_gpio esp_timer
                        INCLUDE_DIRS ".")
