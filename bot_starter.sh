#!/bin/bash
## Simple shell script that reboots the bot if it crashes
until python3 Magic2.py; do
	echo $(date +%H:%M:%S_%Y-%m-%d) "CRASH" | tee -a bot.log
	sleep 1
done
