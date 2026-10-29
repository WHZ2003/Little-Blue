# dsh-video-storyboard live integration

Source: https://github.com/SAOS-3624/dsh-video-storyboard

Pinned commit: `0c03a7d78307d7e8e2b8e6bcaa9d1673d79d3333` (package 0.4.3).

`vendor/density.mjs` and `vendor/plan.mjs` are unchanged upstream `src/density.js`
and `src/plan.js` (extensions renamed for portable ESM loading). The MIT license
and upstream NOTICE are retained. No documentation images are redistributed.

`live.mjs` adapts the pure density planner and layout/pixel budget to Coopanion's
existing live capture. It does not install the DSH runtime, downloader or ASR.
Bilibili uses already captured player frames and existing subtitle access.
Mahjong uses a single latest frame at the same pixel budget, preserving live
hand visibility rather than making a grid of stale turns. Pixel budget is a
local image limit, not a promise of 384 billed tokens or a measured cost saving.
