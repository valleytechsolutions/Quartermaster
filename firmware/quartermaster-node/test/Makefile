CC ?= cc
CFLAGS ?= -O1 -g
CFLAGS += -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -I../main
SRC = test_main.c ../main/proto.c ../main/seen.c ../main/wire.c ../main/enow.c
OUT ?= build/node-tests

test: $(OUT)
	$(OUT)

$(OUT): $(SRC) ../main/proto.h ../main/seen.h ../main/wire.h ../main/enow.h
	mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -o $@ $(SRC)

.PHONY: test
