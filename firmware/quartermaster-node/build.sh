#!/bin/sh
set -e
cd "$(dirname "$0")"
for target in ${*:-esp32c5 esp32c3 esp32c6 esp32s3}; do
    dir="build-$target"
    idf.py -B "$dir" -D SDKCONFIG="$dir/sdkconfig" set-target "$target"
    idf.py -B "$dir" -D SDKCONFIG="$dir/sdkconfig" build
    idf.py -B "$dir" -D SDKCONFIG="$dir/sdkconfig" merge-bin -o quartermaster-node-merged.bin
    idf.py -B "$dir" -D SDKCONFIG="$dir/sdkconfig" size
done
