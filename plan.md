# Plan

## Goal

1/10 scale RC car lap timer

## Requirements

- Track width: 3 m
- Car speed: ~30 kph
- Reader location: side-mounted to track
- Transponder: on/inside the car (1/10 scale RC cars)
- Cars: up to 8, each with unique ID
- Timing precision: within 0.1 second
- Environment: outdoor, dry
- Detection distance: up to 3 m lateral from side-mounted reader

## Hardware

- Car: Seeed XIAO ESP32C3 connected to ESC BEC
- Scanner: Framework 13 7840U with Bluetooth 5.2 running NixOS

## Implementation

- Use latest stable Python

### Bluetooth

- Use `bleak` for bluetooth implementation
- Use RSSI peak to determine timestamp per lap
- Use 20ms as baseline bluetooth time, with prime numbers for further cars to minimise collisions

## UI / UX

- Use `Textual` for UI
- Use a dark theme for UI
- Use a mono font that has retro look
- Has a Start & Stop action
- Once press Start, it will show a 3 second countdown, before showing Go and recording lap times
- It won't record a lap time for a period after race starts to prevent accidental laps
- Active cars have a white, while inactive cars are grey
- Car's default names are One, Two, Three, Four, etc.
- Cars are listed in table, with a row per lap time detected
- Below their times, show the top 5 times for the day for that car
- Lap times should be shown in ss:mm format
- Total race time should be shown in mm:ss:mm format
- Defaults are:
    - Car One only enabled by default on startup
    - 3 laps
    - 3 seconds before starting to detect laps from race start
- Leverage keyboard shortcuts to make navigating UI easier
    - 1-8 to select a car
        - D to disable the car from being monitored
        - R to rename the car from default name
    - C to enter configuration
        - L with a number to set laps before race finishes
        - L with a D to disable lap counting
        - M for minimum signal strength to start determining if lap was completed
    - H to enter history
        - C followed by a car number to clear that cars lap times
        - A to clear all cars lap times
    - E to export the cars recorded data to a CSV file

