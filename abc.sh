#!/bin/bash

while read -r line; do
    echo "$line"
done < <(sleep 100 && ls )

while true; do
    ehco "run"
    sleep 1
done