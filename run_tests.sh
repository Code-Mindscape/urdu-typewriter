#!/bin/bash
set -e

echo "Starting test suite..."

for test_file in test_*.py; do
    [ -e "$test_file" ] || continue
    echo "Running $test_file..."
    python3 "$test_file"
done

echo "All tests passed successfully!"
