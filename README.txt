v34.8 (cont.) — NIGHTLY MEMORY
- 11/10's nightly was killed at 534 of 537 MB. L2 no longer keeps a second in-memory copy of every payload (index + lazy load, 200-entry cap);
  the nightly drops finished stages' payloads and runs gc between stages; the earnings warm-up runs single-threaded without strangle pricing during the nightly.

