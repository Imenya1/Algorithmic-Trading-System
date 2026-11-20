# Throughput baseline note (2026-11-19)
The first baseline (synthetic data, 20,000 candles per stream) was recorded before the gap-handling change, at roughly 180k-199k candles/s.
After the change the 20% regression rule failed (about 132k-148k c/s). An A/B test on the same machine, alternating the pre-gap and gap versions, gave:
pre-gap 162k and 185k c/s; gap version 170k and 156k c/s. Run-to-run noise on this shared machine (about +/-15%) is as large as the difference, so no clear regression was established.
The step now keeps an O(1) running gap counter instead of rescanning the window. The baseline was therefore re-recorded deliberately for the new implementation.
Re-measure on the production hardware and replace the baseline there; the 20% rule only means something when baseline and check run on the same machine.
