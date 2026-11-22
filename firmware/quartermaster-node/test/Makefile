CC ?= cc
CFLAGS ?= -O1 -g
CFLAGS += -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -I../main
SRC = test_main.c ../main/proto.c ../main/seen.c
OUT ?= build/node-tests

test: $(OUT)
	$(OUT)

$(OUT): $(SRC) ../main/proto.h ../main/seen.h
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -o $@ $(SRC)

.PHONY: test
