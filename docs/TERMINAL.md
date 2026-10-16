# Terminal dashboard

Run `chipflow` or `chipflow dashboard` in a Linux terminal. No HTTP server is required.
Use `--run RUN_ID_OR_UNIQUE_PREFIX --view logs` to select a specific job. Place
`--runs /path/to/history` before the subcommand to share another store.

The minimum size is 64 columns by 18 rows. At 100 columns, history and details
appear side by side; narrower terminals show the focused pane. Monochrome terminals
are supported. The refresh interval defaults to one second (`--refresh 0.2` to `60`).

| Key | Action |
| --- | --- |
| Tab | Switch history/details focus |
| Up/Down or j/k | Select runs or scroll details |
| Enter | Focus details and show the run directory |
| 1 / 2 / 3 / 4 | Overview / logs / files / metrics |
| PgUp/PgDn | Scroll details and pause log following |
| Home/End | Beginning/end of retained output |
| f | Pause/resume log following |
| / | Search the focused pane |
| Esc or x | Clear the current search |
| n or p | Launch an example, saved project, or typed configuration path |
| R | Rerun preserved inputs from a terminal-state job |
| c | Cancel an active job, then y to confirm |
| r | Refresh immediately |
| s | Check prerequisites and installation suggestions |
| l | Read the learning path and VLSI glossary |
| ? | Keyboard help |
| q or Ctrl+C | Exit the dashboard without stopping workers |

In the launch form, Up/Down chooses saved projects; Ctrl+U clears the path. Tab
focuses the target selector, where Left/Right cycles stages. Enter validates and
launches. Errors leave the form open. The browser's guided form can create new
project files; `chipflow wizard` creates a configuration interactively for your RTL.
`chipflow quickstart first-chip` creates and simulates a working counter.

Log display initially reads the last 128 KiB and retains up to 8,000 lines. Pausing
keeps the inspected text stationary while the worker continues writing. Search
applies to retained text; `chipflow logs RUN_ID` exports the complete saved log.
Terminal control sequences from native tool output are stripped before rendering.

`[+]` is completed, `[>]` running, `[.]` pending, `[!]` failed/interrupted, and `[x]`
cancelled. Design checks are displayed independently from command completion.
