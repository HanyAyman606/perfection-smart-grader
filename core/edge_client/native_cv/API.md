# OMR Engine — FFI API Reference (for the Flutter side)

This is the complete, exhaustive contract between `omr_engine` (the native library
built from `src/omr_engine.cpp`, declared in
[`include/omr_engine/ffi_api.h`](include/omr_engine/ffi_api.h)) and whatever calls
it over `dart:ffi`. If a value can come out of this library, it's listed here —
nothing is left implicit.

Load the library as:
- Windows: `omr_engine.dll`
- Linux: `omr_engine.so`
- macOS: `omr_engine.dylib`

(All three are produced with no `lib` prefix — see `native_cv/CMakeLists.txt`.)

There are 4 exported functions. Every `const char*` parameter/return is a
null-terminated UTF-8 string.

```c
const char* bs_calibrate(const char* requestPath);
const char* bs_run(const char* imagePath, const char* profilePath, const char* debugImagePath);
int         bs_profile_status(const char* profilePath);
void        bs_free_string(const char* ptr);
```

## Memory ownership (read this first)

`bs_calibrate` and `bs_run` both heap-allocate the string they return (`malloc`).
**Every pointer they return must be passed to `bs_free_string` exactly once**, after
you've copied its contents into a Dart `String` (e.g. via
`ptr.cast<Utf8>().toDartString()`). Never call `free()`/`calloc.free()` on it
directly, never free it twice, never free a pointer that didn't come from one of
these two functions.

`bs_profile_status` and `bs_free_string` do not allocate anything for you to manage.

---

## `bs_calibrate(requestPath)`

Run **once per new physical template** (a new sheet layout, or a materially
different camera/lighting setup) — not once per scanned sheet. This is the
"teach the engine what this sheet looks like" step, driven by a human clicking
bubble corners on a photo of a **blank** reference sheet.

### Input

`requestPath` — path to a file **you must write to disk before calling this**,
in this exact shape (YAML shown; a `.json` file with the same keys also works —
the format is auto-detected from the extension):

| Field | Type | Required | Meaning |
|---|---|---|---|
| `image_path` | string | yes | Absolute path to the **blank** reference photo used for calibration. |
| `profile_path` | string | yes | Absolute path where the learned profile will be written. This is the ONE thing you need to remember afterwards — store it next to the template's name/id in your own storage, you'll pass it to `bs_run` later. |
| `num_questions` | int | yes | Total scored questions on the sheet (e.g. `25`). `0` if the sheet has no MCQ section. |
| `choices_per_question` | int | yes | Options per question (e.g. `4` for A–D). |
| `questions_per_block` | int | yes | Rows in one visual answer column before it wraps to a new one (e.g. `10` for a Q1–10 \| Q11–20 \| Q21–25 layout). |
| `id_letter_count` | int | yes | Rows in the student-ID LETTER bubble column. `0` if the sheet has no letter column. |
| `id_digit_columns` | int | yes | How many digit columns the student ID has (each is always 10 rows, digits 0–9). `0` if there's no numeric ID. |
| `id_letter_labels` | string[] | no | `id_letter_count` strings, one per row top-to-bottom — the actual printed letters (e.g. `["C","D","E","F","M","W"]`, NOT necessarily A,B,C,...). Omit/empty to default to `A,B,C,...`. |
| `blocks_clicks` | list | yes | See below. |

`blocks_clicks` — **either** the reduced 2-click form (recommended) **or** the
explicit per-block form. Don't mix a partial explicit set with a reduced click for
the same group (supplying any explicit name for a group suppresses the reduced
form's auto-expansion for that whole group).

Reduced form (one entry per group, not per column):
- `{name: "id", x1, y1, x2, y2}` — only if `id_letter_count>0` or `id_digit_columns>0`. `(x1,y1)` = center of row 0 of the LETTER column (or `digit_1` if there's no letter column); `(x2,y2)` = center of the very last visible ID bubble (bottom of whichever ID column is tallest).
- `{name: "answers", x1, y1, x2, y2}` — only if `num_questions>0`. `(x1,y1)` = center of Q1 option A; `(x2,y2)` = center of the very last question's last option (e.g. Q25/D).

Explicit form (only if the reduced form's auto-detection fails on an unusual
layout): one `{name, x1, y1, x2, y2}` per `"letter"`, `"digit_1"`, `"digit_2"`, ...,
`"answers_1"`, `"answers_2"`, ... .

All `x`/`y` are **pixel coordinates in `image_path`'s own resolution** (not
normalized) — exactly what a Flutter tap handler reads off the displayed image,
remapped to the original image's pixel space if you scaled it down for display.

### Output

Always this shape:

```json
{ "success": true | false, "error": "string", "log": ["string", ...] }
```

| Field | Meaning |
|---|---|
| `success` | `false` means calibration did not complete and no profile was written. `true` means `profile_path` now exists and is usable by `bs_run`. |
| `error` | Empty string `""` when `success` is `true`. One of the exact messages below when `false`. |
| `log` | Diagnostic trail — **never fatal by itself**, always present (possibly empty only if calibration failed before any block was processed). Surface it in a "calibration details" panel. See the full list of possible entries below. |

#### All possible `error` values (only present/non-empty when `success: false`)

| Error | When it happens |
|---|---|
| `"requestPath is null"` | You passed a null pointer for `requestPath`. |
| `"Failed to load calibration request: <path>"` | The file at `requestPath` doesn't exist, isn't valid YAML/JSON, or is missing required fields. |
| `"Could not read image: <image_path>"` | `image_path` from the request doesn't exist or isn't a readable image. |
| `"Missing calibration clicks for block: <name>"` | After expanding the reduced-click form, some required block (e.g. `"digit_2"`) still has no click-pair — usually a malformed `blocks_clicks` entry. |
| `"Could not detect any real bubble contours near the clicked anchors for block '<name>' — check click accuracy or image quality."` | Every expected bubble position in that block came up empty — the clicks are likely on the wrong spot, or the image quality/resolution is too poor to trace bubble contours at all. |
| `"Failed to write profile to <profile_path>"` | Can't write the output file — bad path or no write permission. |

#### All possible `log` entries (informational; order shown is the order they're generated)

These appear regardless of the final `success` value, up to whichever point
processing reached before any hard error. Bracketed parts vary per run.

- `"answers: combined click expanded into <N> sub-block(s) via detected vertical dividers"` — only when using the reduced `"answers"` click.
- `"id: combined click expanded into <N> column(s)"` — only when using the reduced `"id"` click.
- `"<block>: click snapped to nearest bubble (top-left moved <X>px, bottom-right moved <Y>px)"` — only when a click needed to be snapped more than 3px to line up with a real bubble; large values here are worth surfacing to the calibrating human as a "your click was imprecise" signal.
- `"WARNING: <block> cell (<r>,<c>) has a high baseline fill (<X>) — verify the calibration image is truly blank there."` — the reference sheet may not actually be blank at that exact bubble (a print artifact, or an accidental mark) — the produced profile is still saved, but that cell's sensitivity may be degraded.
- `"<block>: avg_area=<X> avg_aspect=<X> avg_extent=<X> baseline_fill=<X> margin=<X> marked_threshold=<X> (<matched>/<total> matched)"` — one per block, always present. `<matched>/<total>` less than 100% is worth flagging (means some expected bubble position found no contour at all during calibration).
- Exactly one of:
  - `"Detected <N> border-box registration anchor(s) — later photos will auto-correct for camera position/zoom shifts."`
  - `"WARNING: no border-box registration anchors detected in the calibration image — production runs will assume every later photo frames the sheet at the same position/zoom as this calibration photo."` — worth surfacing: it means later `bs_run` calls will be more sensitive to the photo being framed differently than the calibration photo.
- `"Calibration complete: <matched>/<total> bubbles matched."` — always the last entry, unless...
- `"WARNING: <N> expected bubble(s) had no matching contour within range — double-check the anchor clicks for accuracy."` — ...appended after it, only if `N > 0`.

---

## `bs_run(imagePath, profilePath, debugImagePath)`

Run **once per scanned sheet** — the actual grading step.

### Input

| Param | Required | Meaning |
|---|---|---|
| `imagePath` | yes (non-null) | Absolute path to the photo of a filled-in sheet to grade. |
| `profilePath` | yes (non-null) | Absolute path to the `profile_path` a prior `bs_calibrate` call wrote for this template. |
| `debugImagePath` | no | Absolute path to write a visual overlay PNG to. Pass `""` (empty string, **not** null) to skip generating it. Overlay colors: green = accepted answer, blue = matched-but-unmarked bubble, red = no contour matched at all, orange = rejected (ink present but not bubble-shaped). |

### Output

Always this shape, **even on failure** — `letter`/`digits`/`answers`/`warnings`/
`registration` are always present with sensible defaults, so you never need to
null-check them, only check `success`:

```json
{
  "success": true,
  "error": "",
  "letter": "E",
  "digits": ["1", "2", "7"],
  "answers": ["A", "B", "multiple_marks", "rejected", "blank", "..."],
  "warnings": ["Q16: more than one bubble is clearly filled (B/C) — flagged for manual review instead of guessing."],
  "registration": {
    "matched": true,
    "matched_boxes": 2,
    "scale_x": 0.80, "scale_y": 0.80, "offset_x": 12.97, "offset_y": 12.23
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `success` | bool | `false` means the image or profile couldn't even be loaded — see `error`. **When `false`, `digits`/`answers`/`warnings` are empty arrays (`[]`), not sized to the profile's question/digit counts** — the profile was never read. |
| `error` | string | `""` whenever `success` is `true`. One of the exact messages below when `false`. |
| `letter` | string | See all possible values below. Absent entirely (never appears as a key... no — it's always present) — value is `"blank"` if the template has no letter column (`id_letter_count: 0`). |
| `digits` | string[] | Length always equals the profile's `id_digit_columns` (when `success: true`), left to right. Each entry: see below. |
| `answers` | string[] | Length always equals the profile's `num_questions` (when `success: true`), in question order. Each entry: see below. |
| `warnings` | string[] | Human-readable manual-review flags. **Never fatal** — `success` can be `true` with a non-empty `warnings` list. See all possible templates below. |
| `registration.matched` | bool | `false` means no border-box anchors could be matched against the calibration photo, so this photo's framing is *assumed* identical to the calibration photo's — results are less trustworthy when this is `false` (no automatic position/zoom correction happened). |
| `registration.matched_boxes` | int | How many border boxes were successfully matched (0 if `matched` is `false`). |
| `registration.scale_x`, `scale_y` | double | Per-axis scale factor applied to go from the calibration photo's coordinate space to this photo's. `1.0` when `matched` is `false`. |
| `registration.offset_x`, `offset_y` | double | Per-axis pixel offset applied alongside the scale. `0.0` when `matched` is `false`. |

#### All possible `letter` values

- One of the template's `id_letter_labels` (e.g. `"C"`, `"D"`, `"E"`, ... — **not necessarily `"A"`/`"B"`/`"C"`**, this is template-specific; check the profile you calibrated, not this doc, for the exact set).
- `"blank"` — no mark cleared the fill threshold for any row (or the template has no letter column at all).
- `"multiple_marks"` — two or more rows are clearly, comparably filled. **Never auto-resolved to a guess — route to manual review.**
- `"rejected"` — something is marked, but its shape doesn't look like a filled bubble (an X, checkmark, slash, or a circled-but-hollow bubble). **Never auto-resolved — route to manual review.**

#### All possible `digits[i]` values

- `"0"` through `"9"`.
- `"blank"`, `"multiple_marks"`, `"rejected"` — same meanings as above, per digit column.

#### All possible `answers[i]` values

- `"A"` through the `choices_per_question`-th letter (e.g. `"A".."D"` for 4 choices — if a template ever has more than 26 choices this would exceed `'Z'`, which does not happen in practice for a bubble sheet).
- `"blank"`, `"multiple_marks"`, `"rejected"` — same meanings as above, per question.

#### All possible `error` values (only present/non-empty when `success: false`)

| Error | When it happens |
|---|---|
| `"imagePath/profilePath is null"` | You passed a null pointer for `imagePath` or `profilePath`. |
| `"Failed to load profile: <profilePath>"` | The file at `profilePath` doesn't exist or isn't a valid profile (e.g. wasn't produced by `bs_calibrate`, or is corrupted). |
| `"Could not read image: <imagePath>"` | `imagePath` doesn't exist or isn't a readable image. |

#### All possible `warnings[]` entries (never fatal; `success` can still be `true`)

- `"LETTER: the marked bubble doesn't look like a filled-in bubble (looks like an X, checkmark, slash, or circled-not-filled mark) — flagged for manual review."` — paired with `"letter": "rejected"`.
- `"LETTER: two or more bubbles are clearly filled — flagged for manual review instead of guessing."` — paired with `"letter": "multiple_marks"`.
- `"<digit_i>: the marked bubble doesn't look like a filled-in bubble (looks like an X, checkmark, slash, or circled-not-filled mark) — flagged for manual review."` — paired with that digit's `"rejected"` (e.g. `"digit_2: ..."`).
- `"<digit_i>: two or more bubbles are clearly filled — flagged for manual review instead of guessing."` — paired with that digit's `"multiple_marks"`.
- `"Q<n>: only <matched>/<total> choice bubbles matched the calibration profile — check alignment or ink quality."` — appears whenever not every option bubble for that question could be located near its expected position; the question is still graded (using best-effort phantom positions for the unmatched ones), but the result is less trustworthy. **Not mutually exclusive with the other Q-level warnings below** — a question can have both this and a rejected/ambiguous warning.
- `"Q<n>: more than one bubble is clearly filled (<A/B/...>) — flagged for manual review instead of guessing."` — paired with `answers[n-1] == "multiple_marks"`; the parenthesized letters are exactly the contending options.
- `"Q<n>: marked option <X> doesn't look like a filled-in bubble (looks like an X, checkmark, slash, or circled-not-filled mark) — flagged for manual review."` — paired with `answers[n-1] == "rejected"`; `<X>` is the option letter that had the (disqualified) ink.

---

## `bs_profile_status(profilePath)`

Cheap synchronous check for whether a template has already been calibrated — e.g.
to decide whether the UI should offer "scan" or force "calibrate first".

- Returns `1` if `profilePath` exists, is a well-formed profile, and defines at
  least one block.
- Returns `0` otherwise (file missing, unreadable, corrupted, or empty) — including
  when `profilePath` is null.

No error detail is returned here by design — it's a boolean gate, not a
diagnostic. If you need to know *why* a profile is unusable, calibrate again with
`bs_calibrate` and read its `error`.

---

## `bs_free_string(ptr)`

Frees a string previously returned by `bs_calibrate` or `bs_run`. Call exactly
once per returned pointer, after you've extracted what you need from it.

---

## Worked examples

**Successful calibration:**
```json
{"success":true,"error":"","log":["id: combined click expanded into 4 column(s)","letter: avg_area=530.3 avg_aspect=0.99 avg_extent=0.73 baseline_fill=0.24 margin=0.20 marked_threshold=0.44 (6/6 matched)","Detected 2 border-box registration anchor(s) — later photos will auto-correct for camera position/zoom shifts.","Calibration complete: 136/136 bubbles matched."]}
```

**Failed calibration (bad image path):**
```json
{"success":false,"error":"Could not read image: C:\\bad\\path.jpg","log":[]}
```

**Successful grading run with a couple of flagged questions:**
```json
{"success":true,"error":"","letter":"E","digits":["1","2","7"],"answers":["A","B","B","B","A","C","B","D","A","B","A","D","C","A","blank","multiple_marks","A","D","A","C","multiple_marks","B","C","B","A"],"warnings":["Q16: more than one bubble is clearly filled (B/C) — flagged for manual review instead of guessing.","Q21: more than one bubble is clearly filled (A/C) — flagged for manual review instead of guessing."],"registration":{"matched":true,"matched_boxes":2,"scale_x":0.800147,"scale_y":0.799023,"offset_x":12.9672,"offset_y":12.2255}}
```

**Failed run (profile not found):**
```json
{"success":false,"error":"Failed to load profile: does_not_exist.yml","letter":"blank","digits":[],"answers":[],"warnings":[],"registration":{"matched":false,"matched_boxes":0,"scale_x":1,"scale_y":1,"offset_x":0,"offset_y":0}}
```
