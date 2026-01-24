#!/bin/bash

./build/nr-gnb -c config/gnb.yaml &
GNB_PID=$!
sleep 2

sudo ./build/nr-ue -c config/ue-1.yaml &
UE_PID=$!

sleep 2

echo "--- Killing UE (PID: $UE_PID) ---"
sudo kill $UE_PID

trap "echo ' Killing gNB...'; kill $GNB_PID; exit" SIGINT

echo "--- gNB still on. CTRL + C to stop ---"
wait $GNB_PID