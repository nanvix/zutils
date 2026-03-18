# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

CC      ?= gcc
CFLAGS  ?= -Wall -Wextra -Werror -std=c11
SRC_DIR  = src
BUILD_DIR = build
TARGET   = $(BUILD_DIR)/hello

SRCS = $(wildcard $(SRC_DIR)/*.c)
OBJS = $(patsubst $(SRC_DIR)/%.c,$(BUILD_DIR)/%.o,$(SRCS))

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

$(BUILD_DIR)/%.o: $(SRC_DIR)/%.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR)
