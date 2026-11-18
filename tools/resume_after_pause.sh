#!/bin/zsh
# Restarts the Intern Radar widget and background sweep after a pause, then removes its own one-shot job.
U=$(id -u); A=$HOME/Library/LaunchAgents
for j in widget sweep; do [ -f $A/local.internradar.$j.plist.paused ] && mv $A/local.internradar.$j.plist.paused $A/local.internradar.$j.plist; done
launchctl bootstrap gui/$U $A/local.internradar.widget.plist 2>/dev/null
launchctl bootstrap gui/$U $A/local.internradar.sweep.plist 2>/dev/null
echo "$(date) Intern Radar resumed after pause" >> $HOME/Library/Logs/InternRadar.status
rm -f $A/local.internradar.resume.plist
launchctl bootout gui/$U/local.internradar.resume 2>/dev/null
