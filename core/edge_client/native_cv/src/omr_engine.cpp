#include <opencv2/opencv.hpp>
#include <vector>
#include <string>
#include <iostream>
#include <fstream>
#include <sstream>
#include <cmath>
#include <algorithm>
#include <map>
#include <cstring>
#include <cstdlib>
#include <cstdio>

using namespace cv;
using namespace std;

// Public FFI surface (BS_API macro + the four bs_* declarations and their full
// contract doc comments) lives in this header, not here — see that file for what
// Flutter/Dart FFI actually calls. Kept out of this .cpp so the declared contract has
// exactly one source of truth regardless of how many translation units end up
// implementing or consuming it.
#include "omr_engine/ffi_api.h"

// =============================================================================
// OVERVIEW
// =============================================================================
// This backend no longer guesses bubble-sheet geometry from whitespace gaps or
// contour clustering — that heuristic pipeline broke every time paper stock,
// camera resolution, or lighting changed (see history: LETTER column silently
// vanishing, duplicate contours, etc). Production accuracy instead comes from a
// per-template CALIBRATION PROFILE: once per new sheet layout / camera setup, a
// human anchors each grid block with two clicks (top-left bubble center, bottom-
// right bubble center) via whatever UI hosts this tool (Flutter sends pixel
// coordinates — no interactive window lives here). From those two points plus
// the known row/col count we interpolate every bubble center, sample real
// contours at those centers to learn this template's bubble size/shape, and
// save it all normalized (0..1 of image width/height) so any later capture at a
// different resolution rescales correctly, as long as the framing (handled by
// Flutter, no perspective warp here) stays consistent.
//
// Modes:
//   status    <profile.json>                         -> prints EXISTS or MISSING
//   calibrate <calibration_request.json>              -> writes a profile.json
//   run       <image> <profile.json> [out.json] [debug.png]
//
// =============================================================================
// GEOMETRY PRIMITIVES
// =============================================================================
struct Bubble {
    Rect bbox;
    Point2f center;
};

double iou(const Rect& a, const Rect& b) {
    double interArea = (a & b).area();
    double unionArea = (double)a.area() + (double)b.area() - interArea;
    return unionArea > 0 ? interArea / unionArea : 0.0;
}

void normalizeIllumination(const Mat& gray, Mat& out) {
    Mat bg;
    int k = max(31, (min(gray.cols, gray.rows) / 20) | 1);
    GaussianBlur(gray, bg, Size(k, k), 0);
    
    Mat gray32, bg32, div32;
    gray.convertTo(gray32, CV_32F);
    bg.convertTo(bg32, CV_32F);
    divide(gray32, bg32 + 1.0f, div32);
    
    div32 = div32 * 255.0f;
    div32.convertTo(out, CV_8U);
    
    Ptr<CLAHE> clahe = createCLAHE(2.0, Size(8, 8));
    clahe->apply(out, out);
}

void preprocess(const Mat& bgr, Mat& thresh, Mat& gray) {
    Mat blurred, rawGray;
    cvtColor(bgr, rawGray, COLOR_BGR2GRAY);
    normalizeIllumination(rawGray, gray);
    GaussianBlur(gray, blurred, Size(5, 5), 0);
    adaptiveThreshold(blurred, thresh, 255, ADAPTIVE_THRESH_GAUSSIAN_C, THRESH_BINARY_INV, 11, 2);
}

double getMedian(vector<double> v) {
    if (v.empty()) return 0.0;
    size_t n = v.size() / 2;
    nth_element(v.begin(), v.begin() + n, v.end());
    return v[n];
}

// Adaptive threshold (used for finding bubble SHAPES across an unevenly-lit page)
// responds to LOCAL contrast, not absolute darkness — a pencil/pen mark large enough
// that its interior is uniformly dark relative to its own neighborhood produces near-
// zero local contrast there, so only a thin ring around the mark's edge survives
// thresholding; raw pixel-counting inside that ring reads a heavily filled bubble as
// barely half-covered. A contour-area-based "extent" doesn't fix this either: an
// UNMARKED printed circle's own outer boundary traces the same kind of large enclosed
// loop, so contourArea can't distinguish "hollow ring" from "solid ink" any better —
// it inflates blank baselines just as much as real marks. The actual fix is to
// threshold FRESH, locally, with Otsu on a small patch of the grayscale image around
// just this one bubble: Otsu picks the split point from that patch's own bimodal
// paper-vs-ink distribution, so a large uniformly-dark blob is correctly read as
// solid foreground rather than falling into adaptive threshold's local-contrast blind
// spot — while a genuinely blank patch (mostly light paper, thin ring/glyph) still
// splits correctly the other way.
// =============================================================================
// MARK SHAPE ANALYSIS (distinguishing a filled bubble from an X / checkmark /
// slash / circled-not-filled bubble / stray scribble)
// =============================================================================
// fillRatio alone only measures HOW MUCH ink sits inside a bubble, not WHAT SHAPE
// that ink is. That's enough to tell "marked" from "blank", but it can't tell a
// properly filled-in bubble apart from any other dark mark a person might put there
// instead — an X, a checkmark, a single diagonal slash, or a bubble that's circled
// rather than filled can all accumulate enough ink to clear the marked threshold.
// analyzeMark adds two shape descriptors, both computed from the SAME Otsu-thresholded
// local patch fillRatio already used (see its original rationale above), so shape and
// fill are read off one consistent view of the ink rather than two different
// thresholding passes disagreeing with each other:
//   - solidity: real ink pixels / convex-hull area of that ink. A solid filled disk's
//     hull is basically itself (solidity near 1). An X or checkmark's thin crossing
//     strokes leave most of their own hull unfilled (solidity well under 1).
//   - circularity: 4*pi*area/perimeter^2 of the ink. A disk is the shape that
//     maximizes this (near 1); thin strokes have a long perimeter for their area
//     (circularity well under 1).
// Both use the ACTUAL ink pixel count as their area term, never findContours' own
// contourArea() of an outer boundary — contourArea treats any closed loop as fully
// solid, so a heavy, thick, but still-hollow ring drawn AROUND a bubble (circled
// instead of filled) would otherwise score as a perfect disk. Counting real pixels
// means a hollow ring reads as low-solidity, exactly like an X or slash does.
struct MarkAnalysis {
    double fillRatio = 0.0;
    double solidity = 0.0;
    double circularity = 0.0;
    bool found = false;
};

MarkAnalysis analyzeMark(const Mat& gray, const Bubble& b) {
    MarkAnalysis result;
    if (b.bbox.area() <= 0) return result;
    int pad = max(b.bbox.width, b.bbox.height) / 2;
    Rect patchRect((int)(b.center.x - pad), (int)(b.center.y - pad), pad * 2, pad * 2);
    patchRect &= Rect(0, 0, gray.cols, gray.rows);
    if (patchRect.width < 4 || patchRect.height < 4) return result;

    Mat patch = gray(patchRect);
    Mat localThresh;
    threshold(patch, localThresh, 0, 255, THRESH_BINARY_INV | THRESH_OTSU);

    Mat mask = Mat::zeros(patch.size(), CV_8U);
    int r = (b.bbox.width + b.bbox.height) / 4;
    Point centerInPatch(b.center.x - patchRect.x, b.center.y - patchRect.y);
    circle(mask, centerInPatch, max(2, r - 2), 255, -1);

    Mat ink = localThresh & mask;
    int maskArea = countNonZero(mask);
    if (maskArea <= 0) return result;
    int inkPixels = countNonZero(ink);
    result.fillRatio = (double)inkPixels / (double)maskArea;
    result.found = true;
    if (inkPixels < 4) return result; // too little ink for a shape read — moot, it's blank

    vector<vector<Point>> contours;
    findContours(ink, contours, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE);
    if (contours.empty()) return result;

    // An X or checkmark's crossing strokes can split into more than one external
    // contour under thresholding — pool every contour's points into ONE convex hull
    // (and sum perimeters) so the shape read reflects the whole mark, not just
    // whichever single stroke fragment happened to be largest.
    vector<Point> allPts;
    double totalPerimeter = 0;
    for (auto& c : contours) {
        if (c.size() < 3) continue;
        for (auto& p : c) allPts.push_back(p);
        totalPerimeter += arcLength(c, true);
    }
    if (allPts.size() < 3 || totalPerimeter <= 0) return result;

    vector<Point> hull;
    convexHull(allPts, hull);
    double hullArea = contourArea(hull);
    result.solidity = hullArea > 0 ? min(1.0, (double)inkPixels / hullArea) : 0.0;
    result.circularity = min(1.0, (4.0 * CV_PI * (double)inkPixels) / (totalPerimeter * totalPerimeter));
    return result;
}

double fillRatio(const Mat& gray, const Bubble& b) {
    return analyzeMark(gray, b).fillRatio;
}

// Threshold deliberately generous — a real pencil/pen fill on real paper is never a
// mathematically perfect disk (pixelation, stray overlap with the printed ring, an
// uneven hand), so this only needs to fail shapes that are genuinely NOT a filled
// circle. An X, checkmark, single slash, or circled-but-hollow bubble all land well
// below it in practice; an ordinary filled bubble comfortably clears it.
//
// circularity is still computed (see MarkAnalysis) but deliberately NOT gated on here
// anymore: tested against a real photo where a bubble was filled solid and then had an
// X drawn through it, circularity read 0.896 (high — the X's crossing point sits over
// the existing solid fill and barely perturbs the overall blob shape) while a
// DIFFERENT, genuinely clean but lighter pencil fill elsewhere read 0.268 (low — a
// less fully-inked fill's Otsu-thresholded edge is more irregular). Circularity thus
// provided no real protection against the one X case actually seen, while actively
// false-rejecting a legitimate fill. Solidity alone still catches every case in the
// list above (a checkmark/slash/hollow ring all leave most of their own convex hull
// unfilled) without that failure mode.
const double MIN_MARK_SOLIDITY = 0.55;

bool isCleanFilledMark(const MarkAnalysis& m) {
    return m.found && m.solidity >= MIN_MARK_SOLIDITY;
}

// minFillThresholds is per-option (one per entry in `options`) since different option
// labels (A/B/C/D, or different letters/digits) can have genuinely different baseline
// ink density — the winning option is gated against its OWN learned threshold.
//
// Return codes: >=0 is the chosen option index, -2 is blank, -3 is ambiguous (two or
// more CLEAN options marked with comparably strong fill — callers surface this as
// "multiple_marks", not the word "ambiguous", so the caller (Flutter) can pattern-match
// on it specifically and route to manual review instead of silently guessing), -4 is
// rejected — every option that cleared its fill threshold has a shape that doesn't
// match a filled-in bubble (an X, checkmark, slash, circled-not-filled bubble, or other
// scribble).
//
// A non-clean-shaped option is excluded from contention ENTIRELY rather than only
// being checked when it happens to have the single highest raw fill: the earlier
// version compared shape solely on whichever option had the top fill, so a voided
// mark (crossed out, corrected) sitting right next to a real answer could still win
// outright (if its own ink read higher) or falsely drag the real answer into
// "ambiguous" (if its fill was merely close) — either way losing the actual answer.
// Excluding it up front means a single real, clean fill wins on its own regardless of
// how much raw ink some other, non-bubble-shaped mark carries.
// topIdx reports the single highest-fill option overall (clean or not), used to
// highlight/flag the right bubble even when nothing clean was found (-4) or nothing
// crossed threshold at all (-2).
int pickMarkedOption(const Mat& gray, vector<Bubble>& options, const vector<double>& minFillThresholds,
                      int& topIdx, vector<int>* candidateIdxs = nullptr) {
    vector<MarkAnalysis> marks(options.size());
    double topFill = -1;
    topIdx = -1;
    bool anyAboveThreshold = false;
    int bestIdx = -1, secondIdx = -1;
    double best = -1, second = -1;
    for (size_t i = 0; i < options.size(); i++) {
        marks[i] = analyzeMark(gray, options[i]);
        double f = marks[i].fillRatio;
        if (f > topFill) { topFill = f; topIdx = (int)i; }
        if (f < minFillThresholds[i]) continue;
        anyAboveThreshold = true;
        if (!isCleanFilledMark(marks[i])) continue;
        if (candidateIdxs) candidateIdxs->push_back((int)i);
        if (f > best) { second = best; secondIdx = bestIdx; best = f; bestIdx = (int)i; }
        else if (f > second) { second = f; secondIdx = (int)i; }
    }
    if (bestIdx < 0) return anyAboveThreshold ? -4 : -2; // rejected vs genuinely blank
    // Widened from an earlier 0.15 after a real case (a bubble filled solid, then
    // crossed out with an X, next to a second, genuinely intended fill) kept resolving
    // to the X'd option outright: the X's own ink pushed its measured fill up enough
    // that the gap to the real answer (0.15-0.27 depending on photo/calibration) cleared
    // the old bar. Every other real case checked — dozens of questions across several
    // photos and calibration profiles — never produces a second clean candidate at all
    // (nothing else crosses its own threshold), so this can't newly flag any of those;
    // it only widens the net specifically around "two clean marks somewhat close in
    // fill", which is exactly the ambiguous/needs-review case, not a confident single
    // answer.
    const double MIN_MARGIN = 0.30;
    if (secondIdx >= 0 && best - second < MIN_MARGIN) return -3; // ambiguous
    return bestIdx;
}

// Bilinear interpolation of a rows x cols grid from the (row0,col0) and
// (rowN-1,colN-1) bubble centers — the "2-click method". Axis-aligned only:
// good enough since Flutter frames the sheet consistently and small skew is
// absorbed by the local contour search radius during matching — EXCEPT for a
// single-column block (LETTER, DIGIT_i), where that column isn't available:
// with cols==1, tx would otherwise be forced to 0 for every row, pinning x to
// tl.x and silently discarding br.x entirely. A real photo that isn't perfectly
// upright prints that column at a genuine, consistent diagonal (each row a
// couple pixels left/right of the one above) — nothing to do with skew "noise"
// the search radius could shrug off, since it compounds linearly down the
// column. By the last few rows the search window centers far enough from the
// true bubble that only its near edge survives the crop, undersizing (or
// eventually missing) the contour. Falling back to ty for tx when cols==1 lets
// x drift linearly across rows exactly like y already does, using BOTH clicks
// instead of just the top one.
vector<vector<Point2f>> interpolateGrid(Point2d tl, Point2d br, int rows, int cols) {
    vector<vector<Point2f>> grid(rows, vector<Point2f>(cols));
    for (int r = 0; r < rows; r++) {
        double ty = (rows > 1) ? (double)r / (rows - 1) : 0.0;
        for (int c = 0; c < cols; c++) {
            double tx = (cols > 1) ? (double)c / (cols - 1) : ty;
            grid[r][c] = Point2f((float)(tl.x + (br.x - tl.x) * tx),
                                  (float)(tl.y + (br.y - tl.y) * ty));
        }
    }
    return grid;
}

// Per-block local-search radius from that block's own grid spacing. MUST be computed
// identically in calibration and production: calibration's baseline-fill learning and
// production's classification both call sampleNearestContour/matchBubble with this
// radius, and using a different radius for the same block between the two passes
// changes which contour gets matched (and therefore the measured fill ratio) even on
// the exact same image — which silently invalidates the learned baseline.
double computeSearchRadius(const vector<vector<Point2f>>& grid, int rows, int cols) {
    double spacing = 1e18;
    if (rows > 1) spacing = min(spacing, (double)norm(grid[1][0] - grid[0][0]));
    if (cols > 1) spacing = min(spacing, (double)norm(grid[0][1] - grid[0][0]));
    if (spacing > 1e17) spacing = 60.0;
    return max(12.0, spacing * 0.4);
}

// =============================================================================
// SHEET REGISTRATION (position/zoom correction)
// =============================================================================
// A calibrated grid is anchored at fixed pixel positions from ONE calibration
// photo. That's fine only as long as every later photo frames the sheet
// identically — real phone photos don't: a human moving the camera closer/
// farther or shifting it slightly shifts and rescales the whole sheet within
// the frame, silently invalidating every calibrated anchor (the grid points at
// blank paper instead of bubbles). Rather than a full perspective/deskew
// (still out of scope — Flutter isn't expected to hand over a rotated photo),
// we detect the big, high-contrast rounded-rectangle BORDER BOXES this kind of
// template prints around each block group (e.g. the ID box, the Q1-25 box) in
// both the calibration photo and every later photo, and use them to solve a
// simple per-axis scale+translate fit between the two. That corrects for
// camera position and zoom (the cases actually observed) without pretending to
// solve full deskew.
double iou(const Rect& a, const Rect& b);

// A border box is just a thin printed line, but findContours' "any closed
// loop is treated as filled" behavior already means its outer trace encloses
// almost the box's full bounding-box area — no different in principle from
// the same behavior we rely on elsewhere for bubble rings. In a real photo
// that line is rarely perfectly continuous under adaptive thresholding
// (blur/lighting gradients fragment it into arcs), so a morphological close
// bridges those small gaps before contour tracing. Kernel size is sized
// relative to the image's own resolution rather than a fixed pixel count —
// a fixed kernel tuned for one photo's resolution/gap size is exactly the
// kind of hardcoded assumption that breaks on the next photo.
// Safely extracts the 4 extreme corners from a set of 4 points.
vector<Point2f> sortCorners(const vector<Point2f>& pts) {
    Point2f tl, tr, br, bl;
    double min_sum = 1e9, max_sum = -1e9;
    double min_diff = 1e9, max_diff = -1e9;

    for (const auto& pt : pts) {
        double sum = pt.x + pt.y;
        double diff = pt.x - pt.y;

        if (sum < min_sum) { min_sum = sum; tl = pt; }
        if (sum > max_sum) { max_sum = sum; br = pt; }
        if (diff < min_diff) { min_diff = diff; bl = pt; }
        if (diff > max_diff) { max_diff = diff; tr = pt; }
    }
    return {tl, tr, br, bl};
}

vector<Point2f> findCornerSquares(const Mat& raw) {
    Mat gray, blurred, thresh;
    if (raw.channels() == 3) cvtColor(raw, gray, COLOR_BGR2GRAY);
    else gray = raw.clone();

    GaussianBlur(gray, blurred, Size(5, 5), 0);
    adaptiveThreshold(blurred, thresh, 255, ADAPTIVE_THRESH_GAUSSIAN_C, THRESH_BINARY_INV, 15, 5);

    vector<vector<Point>> contours;
    findContours(thresh, contours, RETR_LIST, CHAIN_APPROX_SIMPLE);

    double imgArea = (double)raw.cols * raw.rows;
    vector<Point2f> candidates;

    for (const auto& c : contours) {
        vector<Point> approx;
        double peri = arcLength(c, true);
        approxPolyDP(c, approx, 0.04 * peri, true);

        if (approx.size() == 4 && isContourConvex(approx)) {
            double area = contourArea(approx);
            Rect b = boundingRect(approx);
            if (area > imgArea * 0.0005 && area < imgArea * 0.05) {
                float aspect = (float)b.width / b.height;
                if (aspect >= 0.7 && aspect <= 1.3) {
                    Point2f center(b.x + b.width / 2.0f, b.y + b.height / 2.0f);
                    candidates.push_back(center);
                }
            }
        }
    }

    vector<Point2f> best4;
    if (candidates.size() >= 4) {
        Point2f imgCorners[4] = {
            Point2f(0, 0), Point2f(raw.cols, 0),
            Point2f(raw.cols, raw.rows), Point2f(0, raw.rows)
        };
        for (int i = 0; i < 4; i++) {
            double bestDist = 1e18;
            Point2f bestPt;
            int bestIdx = -1;
            for (size_t j = 0; j < candidates.size(); j++) {
                double dist = norm(candidates[j] - imgCorners[i]);
                if (dist < bestDist) { bestDist = dist; bestPt = candidates[j]; bestIdx = (int)j; }
            }
            if (bestIdx != -1) {
                best4.push_back(bestPt);
                candidates.erase(candidates.begin() + bestIdx);
            }
        }
    }

    if (best4.size() == 4) {
        return sortCorners(best4);
    }
    return {};
}

// =============================================================================
// BLOCK LAYOUT (shared between calibration and production so block names/row-
// col counts always agree)
// =============================================================================
struct BlockDims { string name; int rows; int cols; };

vector<BlockDims> buildBlockLayout(int numQuestions, int choicesPerQuestion,
                                    int questionsPerBlock, int idLetterCount, int idDigitColumns) {
    vector<BlockDims> layout;
    if (idLetterCount > 0) layout.push_back({"letter", idLetterCount, 1});
    for (int i = 1; i <= idDigitColumns; i++) layout.push_back({"digit_" + to_string(i), 10, 1});

    int remaining = numQuestions;
    int blockIdx = 1;
    while (remaining > 0) {
        int rows = min(questionsPerBlock, remaining);
        layout.push_back({"answers_" + to_string(blockIdx), rows, choicesPerQuestion});
        remaining -= rows;
        blockIdx++;
    }
    return layout;
}

// =============================================================================
// CALIBRATION PROFILE (persisted) + CALIBRATION REQUEST (input)
// =============================================================================
struct BubbleShapeProfile {
    double avgArea = 0, avgAspect = 1.0, avgExtent = 0.7;
    double areaTolLow = 0.55, areaTolHigh = 1.60;
    double aspectTol = 0.35;
    double extentTolLow = 0.55;
    // Learned from the (assumed blank) calibration sheet: how much fillRatio a
    // genuinely unmarked bubble reads in this block — printed option glyphs, ruled
    // borders, and anti-aliasing all contribute ink even with no pencil mark. A fixed
    // MIN_FILL threshold caught that baseline ink as "marked" on any template that
    // prints a letter/digit inside the bubble itself. markedThreshold (baseline +
    // margin) is what production actually gates on.
    double baselineFillRatio = 0.0;
    double markedThreshold = 0.35;
    // Calibration mode's foundational assumption is that EVERY bubble on the reference
    // sheet is unmarked — so any spread across those readings is pure measurement noise
    // (lighting, JPEG artifacts, anti-aliasing), not signal. The margin above baseline is
    // therefore sized statistically from that noise (baseline + 3 standard deviations)
    // instead of a flat guessed constant: a clean, low-noise block earns a tight margin,
    // a noisier photo/block automatically earns a wider one.
    double margin = 0.25;
};

struct GridBlockSpec {
    string name;
    int rows = 0, cols = 0;
    Point2d tlNorm, brNorm; // normalized 0..1 by calibration image width/height
    // Bubble size/shape varies by block (e.g. an ID box's letter bubbles are often
    // rendered larger than a dense answer grid's) — pooling one global average across
    // every block made the tolerance gate fit neither well and reject real contours
    // almost everywhere in production, so each block gets its own tuned shape profile.
    BubbleShapeProfile shape;
    // Baseline (unmarked) fill ratio learned PER CELL (exact row,col), not averaged
    // across a whole option group. A per-option average (e.g. one number for the whole
    // "A" column) dilutes a localized artifact — a shadow or crease sitting across just
    // one or two rows reads higher than its column's average, so the average alone
    // doesn't clear it. Baselining per exact cell instead means production run against
    // the SAME calibration image is self-consistent by construction (threshold =
    // baseline + margin >= baseline, always). Row-major, size rows*cols; empty on
    // profiles saved before this field existed — callers fall back to shape.baselineFillRatio.
    vector<double> baselinePerCell;
    // Margin (the noise buffer added on top of baseline) is still grouped PER OPTION
    // LABEL rather than per cell: a single cell has only one calibration sample, so its
    // own variance can't be estimated, but all rows sharing a column's label (or all
    // digits down one column) collectively give a real noise estimate for that label.
    vector<double> marginPerOption;

    int numOptions() const { return cols > 1 ? cols : rows; }
    int optionIndex(int r, int c) const { return cols > 1 ? c : r; }
    double baselineAt(int r, int c) const {
        int idx = r * cols + c;
        if (idx >= 0 && idx < (int)baselinePerCell.size()) return baselinePerCell[idx];
        return shape.baselineFillRatio;
    }
    double marginAt(int r, int c) const {
        int idx = optionIndex(r, c);
        if (idx >= 0 && idx < (int)marginPerOption.size()) return marginPerOption[idx];
        return shape.margin;
    }
    double markedThresholdAt(int r, int c) const {
        // The cap must never fall below (or too close to) the baseline it's capping —
        // an option whose printed glyph is already dense (baseline near/above 0.6) would
        // otherwise get a threshold LOWER than its own unmarked reading, guaranteeing a
        // false positive on that exact option every time. 0.97 is comfortably under the
        // max possible fillRatio (1.0, a fully black bubble) so it only bites when the
        // margin genuinely would have gone unreachable.
        return min(0.97, baselineAt(r, c) + marginAt(r, c));
    }
};

// Bump this whenever CalibrationProfile's on-disk shape changes (fields added,
// removed, or reinterpreted) — loadProfile refuses to load a mismatched profile
// instead of silently misreading fields a newer/older engine build meant
// differently. A stale profile after an engine update should be a clear
// "recalibrate" prompt in Flutter, not a quietly wrong grade.
static const int OMR_PROFILE_SCHEMA_VERSION = 1;

struct CalibrationProfile {
    int calibWidth = 0, calibHeight = 0;
    int numQuestions = 0, choicesPerQuestion = 0, questionsPerBlock = 0;
    int idLetterCount = 0, idDigitColumns = 0;
    vector<string> idLetterLabels; // defaults to A,B,C... if not provided at calibration time
    vector<GridBlockSpec> blocks;
};

bool saveProfile(const CalibrationProfile& p, const string& path) {
    FileStorage fs(path, FileStorage::WRITE);
    if (!fs.isOpened()) return false;
    fs << "schema_version" << OMR_PROFILE_SCHEMA_VERSION;
    fs << "calib_width" << p.calibWidth;
    fs << "calib_height" << p.calibHeight;
    fs << "num_questions" << p.numQuestions;
    fs << "choices_per_question" << p.choicesPerQuestion;
    fs << "questions_per_block" << p.questionsPerBlock;
    fs << "id_letter_count" << p.idLetterCount;
    fs << "id_digit_columns" << p.idDigitColumns;
    fs << "id_letter_labels" << "[";
    for (auto& l : p.idLetterLabels) fs << l;
    fs << "]";
    fs << "blocks" << "[";
    for (auto& b : p.blocks) {
        fs << "{" << "name" << b.name << "rows" << b.rows << "cols" << b.cols
           << "tl_x" << b.tlNorm.x << "tl_y" << b.tlNorm.y
           << "br_x" << b.brNorm.x << "br_y" << b.brNorm.y
           << "shape" << "{"
           << "avg_area" << b.shape.avgArea
           << "avg_aspect" << b.shape.avgAspect
           << "avg_extent" << b.shape.avgExtent
           << "area_tol_low" << b.shape.areaTolLow
           << "area_tol_high" << b.shape.areaTolHigh
           << "aspect_tol" << b.shape.aspectTol
           << "extent_tol_low" << b.shape.extentTolLow
           << "baseline_fill_ratio" << b.shape.baselineFillRatio
           << "marked_threshold" << b.shape.markedThreshold
           << "margin" << b.shape.margin
           << "}"
           << "baseline_per_cell" << b.baselinePerCell
           << "margin_per_option" << b.marginPerOption
           << "}";
    }
    fs << "]";
    fs.release();
    return true;
}

bool loadProfile(CalibrationProfile& p, const string& path, string* errorOut = nullptr, string* errorCodeOut = nullptr) {
    FileStorage fs(path, FileStorage::READ);
    if (!fs.isOpened()) {
        if (errorOut) *errorOut = "Profile not found or unreadable: " + path;
        if (errorCodeOut) *errorCodeOut = "PROFILE_UNREADABLE";
        return false;
    }

    // A profile with no schema_version field predates this check entirely —
    // treat it the same as a mismatch rather than guessing its shape.
    FileNode versionNode = fs["schema_version"];
    int foundVersion = versionNode.empty() ? 0 : (int)versionNode;
    if (foundVersion != OMR_PROFILE_SCHEMA_VERSION) {
        if (errorOut) {
            *errorOut = "Profile was calibrated with an older/incompatible engine version "
                        "(profile schema " + to_string(foundVersion) + ", engine expects "
                        + to_string(OMR_PROFILE_SCHEMA_VERSION) + "). Please recalibrate.";
        }
        if (errorCodeOut) *errorCodeOut = "PROFILE_OUTDATED";
        return false;
    }

    p.calibWidth = (int)fs["calib_width"];
    p.calibHeight = (int)fs["calib_height"];
    p.numQuestions = (int)fs["num_questions"];
    p.choicesPerQuestion = (int)fs["choices_per_question"];
    p.questionsPerBlock = (int)fs["questions_per_block"];
    p.idLetterCount = (int)fs["id_letter_count"];
    p.idDigitColumns = (int)fs["id_digit_columns"];

    FileNode labelsNode = fs["id_letter_labels"];
    if (labelsNode.isSeq()) {
        for (auto it = labelsNode.begin(); it != labelsNode.end(); ++it) p.idLetterLabels.push_back((string)*it);
    }
    if ((int)p.idLetterLabels.size() != p.idLetterCount) {
        p.idLetterLabels.clear();
        for (int i = 0; i < p.idLetterCount; i++) p.idLetterLabels.push_back(string(1, char('A' + i)));
    }

    FileNode blocksNode = fs["blocks"];
    for (auto it = blocksNode.begin(); it != blocksNode.end(); ++it) {
        FileNode bn = *it;
        GridBlockSpec b;
        b.name = (string)bn["name"];
        b.rows = (int)bn["rows"];
        b.cols = (int)bn["cols"];
        b.tlNorm = Point2d((double)bn["tl_x"], (double)bn["tl_y"]);
        b.brNorm = Point2d((double)bn["br_x"], (double)bn["br_y"]);
        FileNode shapeNode = bn["shape"];
        b.shape.avgArea = (double)shapeNode["avg_area"];
        b.shape.avgAspect = (double)shapeNode["avg_aspect"];
        b.shape.avgExtent = (double)shapeNode["avg_extent"];
        b.shape.areaTolLow = (double)shapeNode["area_tol_low"];
        b.shape.areaTolHigh = (double)shapeNode["area_tol_high"];
        b.shape.aspectTol = (double)shapeNode["aspect_tol"];
        b.shape.extentTolLow = (double)shapeNode["extent_tol_low"];
        b.shape.baselineFillRatio = (double)shapeNode["baseline_fill_ratio"];
        b.shape.markedThreshold = (double)shapeNode["marked_threshold"];
        FileNode marginNode = shapeNode["margin"];
        if (!marginNode.empty()) b.shape.margin = (double)marginNode;
        FileNode baselineNode = bn["baseline_per_cell"];
        if (baselineNode.isSeq()) {
            for (auto bit = baselineNode.begin(); bit != baselineNode.end(); ++bit) b.baselinePerCell.push_back((double)*bit);
        }
        FileNode marginOptNode = bn["margin_per_option"];
        if (marginOptNode.isSeq()) {
            for (auto bit = marginOptNode.begin(); bit != marginOptNode.end(); ++bit) b.marginPerOption.push_back((double)*bit);
        }
        p.blocks.push_back(b);
    }

    return true;
}

struct ClickPair { double x1, y1, x2, y2; };

struct CalibrationRequest {
    string imagePath;
    string profilePath;
    int numQuestions = 0, choicesPerQuestion = 0, questionsPerBlock = 0;
    int idLetterCount = 0, idDigitColumns = 0;
    vector<string> idLetterLabels;
    vector<pair<string, ClickPair>> blockClicks; // block name -> anchor clicks (pixel coords)
};

bool loadCalibrationRequest(CalibrationRequest& req, const string& path) {
    FileStorage fs(path, FileStorage::READ);
    if (!fs.isOpened()) return false;
    req.imagePath = (string)fs["image_path"];
    req.profilePath = (string)fs["profile_path"];
    req.numQuestions = (int)fs["num_questions"];
    req.choicesPerQuestion = (int)fs["choices_per_question"];
    req.questionsPerBlock = (int)fs["questions_per_block"];
    req.idLetterCount = (int)fs["id_letter_count"];
    req.idDigitColumns = (int)fs["id_digit_columns"];

    FileNode labelsNode = fs["id_letter_labels"];
    if (labelsNode.isSeq()) {
        for (auto it = labelsNode.begin(); it != labelsNode.end(); ++it) req.idLetterLabels.push_back((string)*it);
    }

    FileNode clicksNode = fs["blocks_clicks"];
    for (auto it = clicksNode.begin(); it != clicksNode.end(); ++it) {
        FileNode cn = *it;
        string name = (string)cn["name"];
        ClickPair cp;
        cp.x1 = (double)cn["x1"]; cp.y1 = (double)cn["y1"];
        cp.x2 = (double)cn["x2"]; cp.y2 = (double)cn["y2"];
        req.blockClicks.push_back({name, cp});
    }
    return true;
}

// =============================================================================
// CONTOUR SAMPLING (shared by calibration extraction + production matching)
// =============================================================================
struct ContourStats {
    bool found = false;
    double area = 0, aspect = 0, extent = 0;
    Point2f center;
    Rect bbox;
};

// Looks for real bubble contours in a small window around `expected`, dedups
// overlapping contours by IoU (a bubble's outer ring and inner fill can both
// produce a contour), and returns whichever surviving contour's centroid is
// closest to `expected`. `looseFilter` controls whether we apply only a very
// permissive circularity/size sanity check (calibration — we don't know the
// template's true bubble shape yet) or nothing extra (production applies its
// own tuned tolerance afterward).
ContourStats sampleNearestContour(const Mat& thresh, Point2f expected, double searchRadius, bool looseFilter) {
    ContourStats best;
    int x0 = max(0, (int)(expected.x - searchRadius));
    int y0 = max(0, (int)(expected.y - searchRadius));
    int x1 = min(thresh.cols, (int)(expected.x + searchRadius));
    int y1 = min(thresh.rows, (int)(expected.y + searchRadius));
    if (x1 <= x0 || y1 <= y0) return best;
    Rect roi(x0, y0, x1 - x0, y1 - y0);

    vector<vector<Point>> contours;
    findContours(thresh(roi), contours, RETR_LIST, CHAIN_APPROX_SIMPLE);

    vector<Bubble> candidates;
    vector<ContourStats> statsList;
    for (auto& cnt : contours) {
        Rect b = boundingRect(cnt);
        b.x += roi.x; b.y += roi.y;
        if (b.width < 6 || b.height < 6) continue;
        float aspect = (float)b.width / b.height;
        double area = contourArea(cnt);
        double extent = area / (double)(b.width * b.height);
        if (looseFilter && (aspect < 0.5 || aspect > 2.0 || extent < 0.1)) continue;

        Bubble bub; bub.bbox = b; bub.center = Point2f(b.x + b.width / 2.0f, b.y + b.height / 2.0f);
        ContourStats s; s.found = true; s.area = area; s.aspect = aspect; s.extent = extent;
        s.center = bub.center; s.bbox = b;

        // A filled bubble routinely produces two overlapping contours (e.g. an inner ink
        // blob plus a slightly larger outer edge picked up by anti-aliasing) — findContours
        // gives no size ordering, so keeping whichever one happens to come first discarded
        // the true, larger contour about as often as not. Keep the bigger of the two.
        //
        // IoU alone misses a second, distinct way this happens: a printed option glyph
        // ("C", "D", ...) drawn in the bubble's center can itself trace as its own small,
        // solid contour concentric with — but nowhere near AS BIG AS — the bubble's own
        // ring. A tiny box nested deep inside a much bigger one has low IoU (union is
        // dominated by the big box) even though it's clearly "the same bubble, different
        // feature" and not an independent second bubble nearby. Nested-containment
        // catches that case IoU can't: if one candidate's box is almost entirely inside
        // the other's, they're the same bubble regardless of their IoU.
        int dupIdx = -1;
        for (size_t i = 0; i < candidates.size(); i++) {
            double interArea = (bub.bbox & candidates[i].bbox).area();
            double containment = interArea / min((double)bub.bbox.area(), (double)candidates[i].bbox.area());
            if (iou(bub.bbox, candidates[i].bbox) > 0.3 || containment > 0.7) { dupIdx = (int)i; break; }
        }
        if (dupIdx == -1) {
            candidates.push_back(bub);
            statsList.push_back(s);
        } else if (area > statsList[dupIdx].area) {
            candidates[dupIdx] = bub;
            statsList[dupIdx] = s;
        }
    }

    double bestDist = 1e18;
    for (auto& s : statsList) {
        double d = norm(s.center - expected);
        if (d < bestDist) { bestDist = d; best = s; }
    }
    return best;
}

// Forward declaration — defined in the PRODUCTION MODE section below, but
// calibration also needs it to learn each block's baseline (unmarked) fill ratio
// using the exact same matching logic production will use.
Bubble matchBubble(const Mat& thresh, Point2f expected, double searchRadius, const BubbleShapeProfile& shape,
                    double areaScale, bool& matched);

// A raw calibration click is rarely pixel-perfect on the true bubble center — a human
// clicking inside a small circle in a cramped ID box routinely lands a few pixels off.
// Left uncorrected, that offset doesn't stay local: interpolateGrid derives EVERY
// bubble in the block from just these two points, so a few pixels of error at one
// corner skews every row between it and the other corner (the exact failure mode this
// session kept re-discovering under different guises). Snap each click to the nearest
// real bubble-shaped contour within a generous radius so ordinary click imprecision
// gets silently corrected instead of baked into the whole block's geometry.
Point2f snapClickToBubble(const Mat& thresh, Point2f rawClick, double snapRadius) {
    auto stats = sampleNearestContour(thresh, rawClick, snapRadius, true);
    return stats.found ? stats.center : rawClick;
}

// =============================================================================
// MULTI-BLOCK ANSWER SECTION AUTO-SPLIT (vertical divider detection)
// =============================================================================
// The answer section is usually printed as one continuous grid (Q1-10 | Q11-20 |
// Q21-25) separated by printed vertical rule lines — rather than making a human click
// top-left/bottom-right of EVERY sub-block (6+ precise clicks deep inside a dense
// grid), we let them click just the overall top-left (row0/colA of the first
// sub-block) and bottom-right (last row/last column of the tallest sub-block) ONCE,
// and recover each sub-block's own boundaries automatically from the printed dividers.

// A vertical rule spans the region continuously top-to-bottom; a column of bubbles
// does NOT (circles leave gaps between rows), so column-wise dark-pixel COVERAGE
// cleanly tells the two apart regardless of the exact rule thickness or bubble size.
// Returns up to `expectedCount` divider x-positions (image coordinates), sorted
// left-to-right; may return fewer if the page doesn't have that many.
vector<double> detectVerticalDividers(const Mat& thresh, double xStart, double xEnd,
                                       double yStart, double yEnd, int expectedCount) {
    int y0 = max(0, (int)yStart), y1 = min(thresh.rows, (int)yEnd);
    int x0 = max(0, (int)xStart), x1 = min(thresh.cols, (int)xEnd);
    if (y1 <= y0 || x1 <= x0 || expectedCount <= 0) return {};
    double height = y1 - y0;

    vector<double> coverage(x1 - x0, 0.0);
    for (int x = x0; x < x1; x++) {
        int dark = 0;
        for (int y = y0; y < y1; y++) if (thresh.at<uchar>(y, x) > 0) dark++;
        coverage[x - x0] = dark / height;
    }

    // Merge adjacent above-threshold columns (a rule is a few px wide) into one peak,
    // keeping the single strongest column from each run as that peak's position.
    vector<pair<double, int>> peaks; // (coverage, x)
    size_t i = 0;
    while (i < coverage.size()) {
        if (coverage[i] > 0.7) {
            size_t j = i;
            double bestCov = coverage[i]; int bestX = (int)i;
            while (j < coverage.size() && coverage[j] > 0.7) {
                if (coverage[j] > bestCov) { bestCov = coverage[j]; bestX = (int)j; }
                j++;
            }
            peaks.push_back({bestCov, bestX + x0});
            i = j;
        } else {
            i++;
        }
    }
    sort(peaks.begin(), peaks.end(), [](auto& a, auto& b) { return a.first > b.first; });
    if ((int)peaks.size() > expectedCount) peaks.resize(expectedCount);

    vector<double> xs;
    for (auto& p : peaks) xs.push_back(p.second);
    sort(xs.begin(), xs.end());
    return xs;
}

// Finds bubble-shaped contours centered within a horizontal band (used to locate a
// sub-block's actual leftmost/rightmost bubble column once divider detection has
// bounded which x-range belongs to it — more robust than guessing a fixed pixel
// offset from the divider, since bubble shape itself pinpoints the true column
// regardless of exactly how much visual margin the template prints on either side).
vector<Point2f> findBubblesInBand(const Mat& thresh, double xStart, double xEnd, double yCenter, double yTolerance) {
    int y0 = max(0, (int)(yCenter - yTolerance)), y1 = min(thresh.rows, (int)(yCenter + yTolerance));
    int x0 = max(0, (int)xStart), x1 = min(thresh.cols, (int)xEnd);
    if (y1 <= y0 || x1 <= x0) return {};
    Rect roi(x0, y0, x1 - x0, y1 - y0);

    vector<vector<Point>> contours;
    findContours(thresh(roi), contours, RETR_LIST, CHAIN_APPROX_SIMPLE);
    vector<Point2f> centers;
    for (auto& c : contours) {
        Rect b = boundingRect(c);
        b.x += roi.x; b.y += roi.y;
        if (b.width < 6 || b.height < 6) continue;
        float aspect = (float)b.width / b.height;
        double area = contourArea(c);
        double extent = area / (double)(b.width * b.height);
        if (aspect < 0.5 || aspect > 2.0 || extent < 0.1) continue;
        Point2f center(b.x + b.width / 2.0f, b.y + b.height / 2.0f);
        // Dedup by x-proximity alone, not full euclidean distance: a bubble ring can
        // fragment into pieces that differ several pixels in y (top vs bottom arc) yet
        // are clearly the same column, whereas two DIFFERENT real columns are never
        // this close in x (spacing is always tens of pixels in every template seen so
        // far) — so a generous x-only threshold merges same-column fragments without
        // any risk of merging two genuinely distinct columns.
        bool dup = false;
        for (auto& e : centers) if (fabs(e.x - center.x) < 15) { dup = true; break; }
        if (!dup) centers.push_back(center);
    }
    sort(centers.begin(), centers.end(), [](const Point2f& a, const Point2f& b) { return a.x < b.x; });
    return centers;
}

// Finds bubble-shaped contours across a (potentially tall) x/y rectangle and groups
// them into rows by y-proximity, returning the BOTTOM-most row's members sorted
// left-to-right. Used instead of searching a narrow band around one guessed y: a
// guess derived from another sub-block's row spacing isn't reliable enough to pin down
// exactly where the true last row sits (this template's answer sub-blocks turned out to
// have a few-percent difference in effective row spacing from column to column — small
// per-row, but compounding to tens of pixels by the last of ~10 rows). Scanning a wide
// span and letting real detected rows cluster themselves only needs that span to be
// generous enough to CONTAIN the true last row somewhere, not to predict its position.
vector<Point2f> findBottomRow(const Mat& thresh, double xStart, double xEnd, double yStart, double yEnd) {
    int x0 = max(0, (int)xStart), x1 = min(thresh.cols, (int)xEnd);
    int y0 = max(0, (int)yStart), y1 = min(thresh.rows, (int)yEnd);
    if (x1 <= x0 || y1 <= y0) return {};
    Rect roi(x0, y0, x1 - x0, y1 - y0);

    vector<vector<Point>> contours;
    findContours(thresh(roi), contours, RETR_LIST, CHAIN_APPROX_SIMPLE);
    vector<Point2f> centers;
    for (auto& c : contours) {
        Rect b = boundingRect(c);
        b.x += roi.x; b.y += roi.y;
        if (b.width < 6 || b.height < 6) continue;
        float aspect = (float)b.width / b.height;
        double area = contourArea(c);
        double extent = area / (double)(b.width * b.height);
        if (aspect < 0.5 || aspect > 2.0 || extent < 0.1) continue;
        Point2f center(b.x + b.width / 2.0f, b.y + b.height / 2.0f);
        // Same x-only dedup rationale as findBubblesInBand above.
        bool dup = false;
        for (auto& e : centers) if (fabs(e.x - center.x) < 15) { dup = true; break; }
        if (!dup) centers.push_back(center);
    }
    if (centers.empty()) return {};

    sort(centers.begin(), centers.end(), [](const Point2f& a, const Point2f& b) { return a.y < b.y; });
    vector<vector<Point2f>> rows(1, vector<Point2f>{centers[0]});
    for (size_t i = 1; i < centers.size(); i++) {
        if (centers[i].y - rows.back().back().y > 10) rows.push_back({centers[i]});
        else rows.back().push_back(centers[i]);
    }
    sort(rows.back().begin(), rows.back().end(), [](const Point2f& a, const Point2f& b) { return a.x < b.x; });
    return rows.back();
}

// A row/question NUMBER LABEL ("21", "22", ...) printed just left of option A can
// itself trace as a small contour that slips past findBubblesInBand's loose bubble
// shape filter — and its shape stats (area, aspect, even contour circularity) turn out
// to sit close enough to genuine small bubbles' own stats at this scale that no
// generic shape test cleanly tells them apart (confirmed empirically: variance-based
// and reference-spacing-based selection among candidate windows both still picked the
// label over the true column in a real case, because the label happened to land
// almost exactly one column-width away — indistinguishable from a real column by
// spacing alone). What DOES reliably distinguish them is domain structure: on every
// bubble-sheet layout, the row/option label precedes the options, never follows
// them — so whenever more candidates are found than there are real options, the
// excess is always extra material to the LEFT, and the true columns are simply the
// RIGHTMOST `expectedCols` candidates in left-to-right order.
vector<Point2f> selectUniformRun(vector<Point2f> pts, int expectedCols) {
    if ((int)pts.size() <= expectedCols || expectedCols < 1) return pts;
    sort(pts.begin(), pts.end(), [](const Point2f& a, const Point2f& b) { return a.x < b.x; });
    return vector<Point2f>(pts.end() - expectedCols, pts.end());
}

// Expands one combined "id" click-pair into per-column ClickPairs (letter, digit_1,
// digit_2, ...). Structurally different from expandAnswersBlock in one important way:
// there, a shorter sub-block (fewer rows) still uses the SAME row-to-row spacing as
// its siblings, just stopping sooner — but here, LETTER's 6 rows and a DIGIT column's
// 10 rows typically span roughly the SAME total box height, meaning LETTER's row
// spacing is genuinely wider, not just truncated. Assuming one shared rowSpacing
// across every ID column (as answers does) would put LETTER's guessed last row
// hundreds of pixels short of its real position. So instead of guessing each column's
// bottom from a shared spacing and refining with a tight snap, every column
// independently scans its own full vertical span and clusters real detections into
// rows, taking whichever cluster is bottom-most — the row COUNT never enters into
// finding it, so a different spacing doesn't matter.
vector<pair<string, ClickPair>> expandIdBlock(const Mat& thresh, Point2f tl, Point2f br,
                                               const vector<BlockDims>& idBlocks) {
    int numCols = (int)idBlocks.size();
    double roughRowSpacing = (idBlocks.back().rows > 1) ? (br.y - tl.y) / (idBlocks.back().rows - 1) : 0.0;
    double yTol = max(8.0, roughRowSpacing * 0.4);
    double outerPad = max(15.0, roughRowSpacing * 0.3);

    auto topBubbles = selectUniformRun(findBubblesInBand(thresh, tl.x - outerPad, br.x + outerPad, tl.y, yTol),
                                        numCols);
    vector<Point2f> colTops(numCols);
    if ((int)topBubbles.size() == numCols) {
        colTops = topBubbles;
    } else {
        // Column band search came up short (sparse/blurry photo) — fall back to
        // evenly-spaced guesses across the overall span rather than failing outright.
        for (int i = 0; i < numCols; i++) {
            double x = (numCols > 1) ? tl.x + (br.x - tl.x) * i / (numCols - 1) : tl.x;
            colTops[i] = Point2f((float)x, tl.y);
        }
    }

    vector<pair<string, ClickPair>> result;
    for (int i = 0; i < numCols; i++) {
        // Half the gap to this column's nearest neighbor, so the scan band stays
        // clear of adjacent columns regardless of how columns happen to be spaced.
        double leftGap = (i > 0) ? colTops[i].x - colTops[i - 1].x : colTops.size() > 1 ? colTops[1].x - colTops[0].x : outerPad * 2;
        double rightGap = (i + 1 < numCols) ? colTops[i + 1].x - colTops[i].x : leftGap;
        double halfBand = max(10.0, min(leftGap, rightGap) * 0.4);

        auto bottomRow = findBottomRow(thresh, colTops[i].x - halfBand, colTops[i].x + halfBand,
                                        tl.y, br.y + outerPad);
        Point2f colBottom = !bottomRow.empty() ? bottomRow.front()
                                                : Point2f(colTops[i].x, (float)br.y);
        result.push_back({idBlocks[i].name, ClickPair{colTops[i].x, colTops[i].y, colBottom.x, colBottom.y}});
    }
    return result;
}

// Expands one combined "answers" click-pair into per-sub-block ClickPairs (answers_1,
// answers_2, ...), splitting on detected divider lines. tl/br must already be the
// SNAPPED overall top-left (row0/colA of the first sub-block) and bottom-right (the
// very last visible bubble overall — last row/last column of the LAST sub-block,
// whatever its row count is). Falls back to naive equal-width splitting for any
// sub-block whose column band search comes up empty (e.g. an unusually sparse/blurry
// photo) rather than failing calibration outright — a rough split is still better
// than none.
vector<pair<string, ClickPair>> expandAnswersBlock(const Mat& thresh, Point2f tl, Point2f br,
                                                    const vector<BlockDims>& answerBlocks, double roughRowSpacing,
                                                    int choicesPerQuestion) {
    int numBlocks = (int)answerBlocks.size();
    vector<double> dividerXs = detectVerticalDividers(thresh, tl.x, br.x, tl.y, br.y, numBlocks - 1);

    vector<double> boundaries; // numBlocks+1 x-positions bounding each sub-block's segment
    boundaries.push_back(tl.x);
    for (double d : dividerXs) boundaries.push_back(d);
    while ((int)boundaries.size() < numBlocks) {
        // Divider detection came up short (e.g. faint print) — fill in evenly-spaced
        // guesses so every sub-block still gets SOME segment to search within.
        double lastGap = (br.x - boundaries.back()) / (numBlocks - boundaries.size() + 1);
        boundaries.push_back(boundaries.back() + lastGap);
    }
    boundaries.push_back(br.x);

    // The outermost two boundaries are the user's OWN click positions — i.e. exactly
    // colA's and colD's true centers, not a gap between blocks. Searching a segment
    // that starts/ends AT a bubble's own center clips the outward half of that exact
    // bubble's contour, which can shrink its measured bbox enough to fail the shape
    // gate entirely (this is what silently dropped the true last column from a real
    // search). Pad outward by a fraction of a row's spacing — generous enough to give
    // that one bubble full clearance, small enough to stay well short of reaching a
    // neighboring column.
    double outerPad = max(15.0, roughRowSpacing * 0.3);
    boundaries.front() -= outerPad;
    boundaries.back() += outerPad;

    double yTol = max(8.0, roughRowSpacing * 0.4);

    // Each sub-block's OWN row0 (not the combined click's y forced onto every block)
    // — the same per-photo drift that shows up as x-drift down a single-choice column
    // elsewhere in this file can just as easily show up as a small y-offset between
    // side-by-side sub-blocks here.
    vector<Point2f> colAs(numBlocks);
    for (int i = 0; i < numBlocks; i++) {
        auto topBubbles = selectUniformRun(findBubblesInBand(thresh, boundaries[i], boundaries[i + 1], tl.y, yTol),
                                            choicesPerQuestion);
        colAs[i] = !topBubbles.empty() ? topBubbles.front() : Point2f((float)boundaries[i], (float)tl.y);
    }

    // Derive row spacing from the LAST sub-block's own (just-found) row0 paired with
    // the user's actual bottom-right click — both real points on the SAME column,
    // unlike pairing block1's row0 with a DIFFERENT block's last row.
    double rowSpacing = (answerBlocks.back().rows > 1)
        ? (br.y - colAs.back().y) / (answerBlocks.back().rows - 1) : roughRowSpacing;
    double scanPad = max(yTol * 3.0, rowSpacing * 1.5);

    vector<pair<string, ClickPair>> result;
    for (int i = 0; i < numBlocks; i++) {
        double segStart = boundaries[i], segEnd = boundaries[i + 1];
        double bottomYGuess = colAs[i].y + rowSpacing * (answerBlocks[i].rows - 1);
        auto bottomRow = selectUniformRun(
            findBottomRow(thresh, segStart, segEnd, bottomYGuess - scanPad, bottomYGuess + scanPad),
            choicesPerQuestion);
        Point2f colD = !bottomRow.empty() ? bottomRow.back() : Point2f((float)segEnd, (float)bottomYGuess);
        result.push_back({answerBlocks[i].name, ClickPair{colAs[i].x, colAs[i].y, colD.x, colD.y}});
    }
    return result;
}

// =============================================================================
// CALIBRATION MODE
// =============================================================================
// logOut is optional (nullptr for CLI use, which keeps relying on the cout lines below):
// the FFI wrapper (bs_calibrate) passes a real vector so it can hand the same diagnostic
// messages back to Flutter as JSON instead of them only ever reaching a console no GUI
// app has attached.
bool runCalibration(const CalibrationRequest& req, string& errorMsg, vector<string>* logOut = nullptr, double* outFiducialRatio = nullptr) {
    auto logLine = [&](const string& s) { cout << s << endl; if (logOut) logOut->push_back(s); };

    Mat raw = imread(req.imagePath);
    if (raw.empty()) { errorMsg = "Could not read image: " + req.imagePath; return false; }

    Mat H; // NEW: Declare H outside the if-block
    vector<Point2f> corners = findCornerSquares(raw);
    if (corners.size() == 4) {
        vector<Point2f> dst = {
            Point2f(0, 0), Point2f(1000, 0),
            Point2f(1000, 1414), Point2f(0, 1414)
        };
        H = getPerspectiveTransform(corners, dst); // Use the outer H
        warpPerspective(raw, raw, H, Size(1000, 1414));
        logLine("Registration: Auto-detected 4 corner squares -> Deskewed to 1000x1414 canvas.");
        if (outFiducialRatio) {
            double w = norm(corners[0] - corners[1]);
            double h = norm(corners[0] - corners[3]);
            *outFiducialRatio = (h > 0) ? (w / h) : 1.414;
        }
    } else {
        errorMsg = "Phase 2 Calibration Failed: Could not detect the 4 corner squares on the image.";
        return false;
    }

    // NEW: Warp the raw Flutter clicks to match the flattened 1000x1414 canvas
    vector<pair<string, ClickPair>> calibratedClicks = req.blockClicks;
    if (!H.empty()) {
        for (auto& bc : calibratedClicks) {
            vector<Point2f> pts = {
                Point2f((float)bc.second.x1, (float)bc.second.y1),
                Point2f((float)bc.second.x2, (float)bc.second.y2)
            };
            vector<Point2f> warpedPts;
            perspectiveTransform(pts, warpedPts, H);
            bc.second.x1 = warpedPts[0].x;
            bc.second.y1 = warpedPts[0].y;
            bc.second.x2 = warpedPts[1].x;
            bc.second.y2 = warpedPts[1].y;
        }
    }

    Mat thresh, gray;
    preprocess(raw, thresh, gray);

    auto layout = buildBlockLayout(req.numQuestions, req.choicesPerQuestion, req.questionsPerBlock,
                                    req.idLetterCount, req.idDigitColumns);

    CalibrationProfile profile;
    profile.calibWidth = raw.cols;
    profile.calibHeight = raw.rows;
    profile.numQuestions = req.numQuestions;
    profile.choicesPerQuestion = req.choicesPerQuestion;
    profile.questionsPerBlock = req.questionsPerBlock;
    profile.idLetterCount = req.idLetterCount;
    profile.idDigitColumns = req.idDigitColumns;
    profile.idLetterLabels = req.idLetterLabels;
    if ((int)profile.idLetterLabels.size() != req.idLetterCount) {
        profile.idLetterLabels.clear();
        for (int i = 0; i < req.idLetterCount; i++) profile.idLetterLabels.push_back(string(1, char('A' + i)));
    }

    // Generous but spacing-safe: enough to absorb ordinary click imprecision without
    // risking a snap onto the WRONG (adjacent) bubble. Sized relative to the image's own
    // resolution rather than a fixed pixel count, same reasoning as everywhere else in
    // this file that a hardcoded pixel constant would silently stop being valid the
    // moment resolution changes.
    double snapRadius = max(15.0, min(raw.cols, raw.rows) * 0.025);

    // Support the reduced-click calibration format: one combined "answers" click-pair
    // instead of a separate click-pair per answers_N sub-block. Only kicks in when the
    // request doesn't already give answers_1 explicitly, so existing per-block
    // calibration_request.yml files keep working unchanged.
    vector<pair<string, ClickPair>> effectiveBlockClicks = calibratedClicks;
    bool hasExplicitAnswers1 = false;
    const ClickPair* combinedAnswersClick = nullptr;
    for (auto& bc : calibratedClicks) {
        if (bc.first == "answers_1") hasExplicitAnswers1 = true;
        if (bc.first == "answers") combinedAnswersClick = &bc.second;
    }
    if (!hasExplicitAnswers1 && combinedAnswersClick) {
        vector<BlockDims> answerBlocks;
        for (auto& dims : layout) if (dims.name.rfind("answers_", 0) == 0) answerBlocks.push_back(dims);
        if (!answerBlocks.empty()) {
            Point2f rawTl((float)combinedAnswersClick->x1, (float)combinedAnswersClick->y1);
            Point2f rawBr((float)combinedAnswersClick->x2, (float)combinedAnswersClick->y2);
            Point2f tl = snapClickToBubble(thresh, rawTl, snapRadius);
            Point2f br = snapClickToBubble(thresh, rawBr, snapRadius);
            // The natural "last click" is the very last visible bubble overall — the
            // LAST sub-block's last row, last column (e.g. Q25/D), which is a real
            // bubble regardless of whether that sub-block is shorter than the others.
            // Row spacing must therefore come from ITS row count, not the first block's
            // — using the first block's (taller) row count here would divide the same
            // pixel span by a bigger number and understate the true spacing whenever
            // the last sub-block is shorter (this template's Q21-25 vs Q1-10/Q11-20).
            double rowSpacing = (answerBlocks.back().rows > 1)
                ? (br.y - tl.y) / (answerBlocks.back().rows - 1) : 0.0;
            auto expanded = expandAnswersBlock(thresh, tl, br, answerBlocks, rowSpacing, req.choicesPerQuestion);
            { ostringstream ss; ss << "answers: combined click expanded into " << expanded.size()
                 << " sub-block(s) via detected vertical dividers"; logLine(ss.str()); }
            for (auto& e : expanded) effectiveBlockClicks.push_back(e);
        }
    }

    // Same reduced-click support for the ID box: one combined "id" click-pair instead
    // of a separate click-pair per column (letter, digit_1, digit_2, ...). Only kicks
    // in when the request doesn't already give letter or digit_1 explicitly.
    bool hasExplicitIdColumn = false;
    const ClickPair* combinedIdClick = nullptr;
    for (auto& bc : calibratedClicks) {
        if (bc.first == "letter" || bc.first == "digit_1") hasExplicitIdColumn = true;
        if (bc.first == "id") combinedIdClick = &bc.second;
    }
    if (!hasExplicitIdColumn && combinedIdClick) {
        vector<BlockDims> idBlocks;
        for (auto& dims : layout) if (dims.name == "letter" || dims.name.rfind("digit_", 0) == 0) idBlocks.push_back(dims);
        if (!idBlocks.empty()) {
            Point2f rawTl((float)combinedIdClick->x1, (float)combinedIdClick->y1);
            Point2f rawBr((float)combinedIdClick->x2, (float)combinedIdClick->y2);
            Point2f tl = snapClickToBubble(thresh, rawTl, snapRadius);
            Point2f br = snapClickToBubble(thresh, rawBr, snapRadius);
            auto expanded = expandIdBlock(thresh, tl, br, idBlocks);
            { ostringstream ss; ss << "id: combined click expanded into " << expanded.size() << " column(s)"; logLine(ss.str()); }
            for (auto& e : expanded) effectiveBlockClicks.push_back(e);
        }
    }

    for (auto& dims : layout) {
        const ClickPair* clicks = nullptr;
        for (auto& bc : effectiveBlockClicks) if (bc.first == dims.name) { clicks = &bc.second; break; }
        if (!clicks) { errorMsg = "Missing calibration clicks for block: " + dims.name; return false; }

        Point2f rawTl((float)clicks->x1, (float)clicks->y1), rawBr((float)clicks->x2, (float)clicks->y2);
        Point2f snappedTl = snapClickToBubble(thresh, rawTl, snapRadius);
        Point2f snappedBr = snapClickToBubble(thresh, rawBr, snapRadius);
        double tlShift = norm(snappedTl - rawTl);
        double brShift = norm(snappedBr - rawBr);
        if (tlShift > 3.0 || brShift > 3.0) {
            ostringstream ss; ss << dims.name << ": click snapped to nearest bubble (top-left moved " << tlShift
                 << "px, bottom-right moved " << brShift << "px)";
            logLine(ss.str());
        }
        Point2d tl(snappedTl.x, snappedTl.y), br(snappedBr.x, snappedBr.y);

        GridBlockSpec spec;
        spec.name = dims.name; spec.rows = dims.rows; spec.cols = dims.cols;
        spec.tlNorm = Point2d(tl.x / raw.cols, tl.y / raw.rows);
        spec.brNorm = Point2d(br.x / raw.cols, br.y / raw.rows);
        profile.blocks.push_back(spec);
    }

    auto mean = [](vector<double>& v) { double s = 0; for (double x : v) s += x; return s / v.size(); };

    int missingCount = 0, totalCount = 0;
    for (auto& spec : profile.blocks) {
        Point2d tl(spec.tlNorm.x * raw.cols, spec.tlNorm.y * raw.rows);
        Point2d br(spec.brNorm.x * raw.cols, spec.brNorm.y * raw.rows);
        auto grid = interpolateGrid(tl, br, spec.rows, spec.cols);
        // Same per-block radius formula production uses (see computeSearchRadius) —
        // keeping calibration and production in lockstep here is what makes the
        // learned baseline fill ratio actually valid at production time.
        double searchRadius = computeSearchRadius(grid, spec.rows, spec.cols);

        // Bubble size/shape is learned PER BLOCK, not pooled globally: an ID box's
        // bubbles and a dense answer grid's bubbles can be genuinely different sizes on
        // the same sheet, and a single pooled average fits neither well enough for the
        // production tolerance gate to accept real contours.
        vector<double> areas, aspects, extents;
        for (int c = 0; c < spec.cols; c++) {
            Point2f curr = grid[0][c];
            for (int r = 0; r < spec.rows; r++) {
                totalCount++;
                auto stats = sampleNearestContour(thresh, curr, searchRadius, true);
                if (stats.found) {
                    areas.push_back(stats.area);
                    aspects.push_back(stats.aspect);
                    extents.push_back(stats.extent);
                } else {
                    missingCount++;
                }

                if (r + 1 < spec.rows) {
                    Point2f step = grid[r+1][c] - grid[r][c];
                    if (stats.found) curr = stats.center + step;
                    else curr = curr + step;
                }
            }
        }
        if (areas.empty()) {
            errorMsg = "Could not detect any real bubble contours near the clicked anchors for block '" +
                       spec.name + "' — check click accuracy or image quality.";
            return false;
        }

        // Robustify against noise BEFORE computing avg/tolerance: a real photo can have a
        // shadow, dust speck, or print artifact land right on an expected bubble center,
        // and sampleNearestContour's loose filter is permissive enough to accept it as a
        // "found" contour. A single such outlier used to corrupt min/max-derived tolerance
        // for the WHOLE block (making it MORE permissive, not less — exactly backwards),
        // which then let production match the same kind of noise elsewhere. Reject any
        // sample whose area isn't within a sane multiple of the block's own median before
        // it can influence avgArea or the tolerance band.
        double medianArea = getMedian(areas);
        vector<double> cleanAreas, cleanAspects, cleanExtents;
        for (size_t i = 0; i < areas.size(); i++) {
            double ratio = areas[i] / medianArea;
            if (ratio >= 0.4 && ratio <= 2.5) {
                cleanAreas.push_back(areas[i]);
                cleanAspects.push_back(aspects[i]);
                cleanExtents.push_back(extents[i]);
            }
        }
        if (cleanAreas.empty()) { cleanAreas = areas; cleanAspects = aspects; cleanExtents = extents; }

        spec.shape.avgArea = mean(cleanAreas);
        spec.shape.avgAspect = mean(cleanAspects);
        spec.shape.avgExtent = mean(cleanExtents);

        // Tolerance bounds are derived from the observed (now outlier-trimmed) spread
        // within this block, with a margin — a block whose contours vary more (different
        // glyphs per option, anti-aliasing noise) naturally earns a wider band, while a
        // very uniform block stays tight.
        double minArea = *min_element(cleanAreas.begin(), cleanAreas.end());
        double maxArea = *max_element(cleanAreas.begin(), cleanAreas.end());
        double minAspect = *min_element(cleanAspects.begin(), cleanAspects.end());
        double maxAspect = *max_element(cleanAspects.begin(), cleanAspects.end());
        double minExtent = *min_element(cleanExtents.begin(), cleanExtents.end());

        spec.shape.areaTolLow = min(0.55, (minArea / spec.shape.avgArea) * 0.85);
        // The upper bound needs much more headroom than the lower one: every area sample
        // here comes from an UNMARKED bubble (calibration's founding assumption), but a
        // genuinely marked bubble in production is inherently bigger/denser — full ink
        // connects with anti-aliasing into a visibly larger blob than an empty ring or a
        // thin printed glyph ever is. A tight upper bound derived only from unmarked data
        // rejects the very thing we need to detect, falling back to an undersized phantom
        // that then under-samples the mark. 3.5x default default gives real marks room to
        // pass the shape gate and get their true (larger) contour used for fill sampling.
        spec.shape.areaTolHigh = max(3.5, (maxArea / spec.shape.avgArea) * 1.15);
        spec.shape.aspectTol = max(0.35, (maxAspect - minAspect) / 2.0 * 1.3);
        spec.shape.extentTolLow = min(0.55, (minExtent / spec.shape.avgExtent) * 0.85);

        // Learn how much ink an UNMARKED bubble reads as, using the same matchBubble()
        // production will call. This assumes the calibration image is a blank (unfilled)
        // reference sheet — calibrating against an already-marked sheet would bake real
        // marks into the "blank" baseline and raise the bar too high. Baseline is stored
        // PER CELL (exact row,col) so a localized artifact (e.g. a shadow crossing just
        // one or two rows) can't get diluted into a column-wide average and leave that
        // one cell under-thresholded — production run against the SAME calibration image
        // is then self-consistent by construction. Margin (the noise buffer) is grouped
        // PER OPTION LABEL instead, since a single cell has only one sample to judge
        // spread from, but all rows sharing a column's label collectively do.
        vector<double> fills(spec.rows * spec.cols);
        int numOptions = spec.numOptions();
        vector<vector<double>> fillsByOption(numOptions);
        vector<Point2f> colStarts(spec.cols);
        for (int c = 0; c < spec.cols; c++) colStarts[c] = grid[0][c];

        for (int c = 0; c < spec.cols; c++) {
            Point2f curr = colStarts[c];
            for (int r = 0; r < spec.rows; r++) {
                bool matched;
                Bubble b = matchBubble(thresh, curr, searchRadius, spec.shape, 1.0, matched);
                fills[r * spec.cols + c] = fillRatio(gray, b);

                if (r == 0 && matched && c + 1 < spec.cols) {
                    colStarts[c + 1] = b.center + (grid[0][c + 1] - grid[0][c]);
                }

                if (r + 1 < spec.rows) {
                    Point2f step = grid[r+1][c] - grid[r][c];
                    if (matched) curr = b.center + step;
                    else curr = curr + step;
                }
            }
        }

        // fillRatio's local-Otsu measurement gets noticeably less stable as bubbles get
        // small (this template's answer bubbles print a letter glyph inside a ~14px
        // circle): an UNMATCHED cell's tiny phantom patch is barely bigger than the
        // printed ring/glyph itself, so small shifts in exactly where Otsu's split lands
        // can swing the reading by 2-3x for what is genuinely the same blank bubble a
        // neighboring cell reads consistently low for. Left alone, a single such noise
        // spike bakes straight into that one cell's baseline (and, via markedThresholdAt's
        // 0.97 cap, can make a REAL mark at that exact cell unrecoverable in production —
        // this is exactly how Q14 on bubble5.jfif went from a clear, dark mark to
        // "blank"). Clip any per-cell reading against the block's own median plus a fixed
        // 0.15 allowance (the same floor already used for margins elsewhere — a block
        // whose genuinely blank cells vary by more than that is not this template's
        // normal behavior) before it can poison baselinePerCell or the per-option
        // variance/margin. A real, spatially-consistent artifact (e.g. a shadow across
        // several rows) would still shift the MEDIAN itself and so isn't defeated by
        // this — only individual cells that spike far above their block's own normal
        // spread get capped.
        double medianFill = getMedian(fills);
        double fillCap = medianFill + 0.15;
        for (double& f : fills) f = min(f, fillCap);
        for (int r = 0; r < spec.rows; r++) {
            for (int c = 0; c < spec.cols; c++) {
                fillsByOption[spec.optionIndex(r, c)].push_back(fills[r * spec.cols + c]);
            }
        }
        spec.baselinePerCell = fills;
        spec.shape.baselineFillRatio = mean(fills);

        // Every one of these readings came from a bubble calibration mode assumes is
        // unmarked, so the spread WITHIN each option's group of samples IS that option's
        // measurement noise floor — lighting, JPEG compression, anti-aliasing. Size each
        // option's margin off its own noise (3 standard deviations, the standard
        // "confidently not noise" bar) rather than a flat guess: a clean synthetic
        // template gets a tight margin, a noisier option automatically earns a wider one.
        // Single-choice columns (LETTER, DIGIT_i: cols == 1) have exactly ONE calibration
        // sample per "option" (optionIndex == row, since each row IS a distinct label) —
        // there's no spread to measure variance from, so this would always bottom out at
        // the 0.15 floor regardless of the column's actual noise. That floor proved too
        // tight in practice (a blank ID-box digit read as marked on a different photo's
        // blur/lighting). Leave marginPerOption empty for these blocks instead, so
        // markedThresholdAt falls back to shape.margin — computed from variance across all
        // `rows` cells in the column, a real sample this time. Multi-choice blocks
        // (answers_N: cols > 1) keep per-option margins, where each option genuinely has
        // `rows` independent samples to estimate noise from.
        if (spec.cols > 1) {
            spec.marginPerOption.resize(numOptions);
            for (int i = 0; i < numOptions; i++) {
                double optMean = mean(fillsByOption[i]);
                double variance = 0;
                for (double f : fillsByOption[i]) variance += (f - optMean) * (f - optMean);
                variance /= fillsByOption[i].size();
                spec.marginPerOption[i] = min(0.5, max(0.15, 3.0 * sqrt(variance)));
            }
        }
        double blockVariance = 0;
        for (double f : fills) blockVariance += (f - spec.shape.baselineFillRatio) * (f - spec.shape.baselineFillRatio);
        blockVariance /= fills.size();
        spec.shape.margin = min(0.5, max(0.15, 3.0 * sqrt(blockVariance)));
        spec.shape.markedThreshold = min(0.97, spec.shape.baselineFillRatio + spec.shape.margin);

        // Sanity check on our own founding assumption: if a cell's baseline already reads
        // high even after its own margin is applied, either this block's ink is unusually
        // dense there (fine, the margin above still clears it) or the "blank" calibration
        // image actually has something marked there (a data problem no margin can fix) —
        // flag it either way so a human can double-check the calibration image.
        for (int r = 0; r < spec.rows; r++) {
            for (int c = 0; c < spec.cols; c++) {
                if (spec.baselineAt(r, c) > 0.6) {
                    ostringstream ss; ss << "WARNING: " << spec.name << " cell (" << r << "," << c << ") has a high baseline fill ("
                         << spec.baselineAt(r, c) << ") — verify the calibration image is truly blank there.";
                    logLine(ss.str());
                }
            }
        }

        {
            ostringstream ss;
            ss << spec.name << ": avg_area=" << spec.shape.avgArea << " avg_aspect=" << spec.shape.avgAspect
               << " avg_extent=" << spec.shape.avgExtent << " baseline_fill=" << spec.shape.baselineFillRatio
               << " margin=" << spec.shape.margin
               << " marked_threshold=" << spec.shape.markedThreshold
               << " (" << areas.size() << "/" << (spec.rows * spec.cols) << " matched)";
            logLine(ss.str());
        }
    }


    if (!saveProfile(profile, req.profilePath)) { errorMsg = "Failed to write profile to " + req.profilePath; return false; }

    { ostringstream ss; ss << "Calibration complete: " << (totalCount - missingCount) << "/" << totalCount << " bubbles matched."; logLine(ss.str()); }
    if (missingCount > 0) {
        ostringstream ss;
        ss << "WARNING: " << missingCount << " expected bubble(s) had no matching contour within range — "
           << "double-check the anchor clicks for accuracy.";
        logLine(ss.str());
    }
    return true;
}

// =============================================================================
// PRODUCTION MODE
// =============================================================================
// areaScale rescales the calibrated avgArea (a pixel-area, which scales with the
// SQUARE of any linear resize) to the current image's resolution — without this, a
// profile calibrated at one resolution rejects real contours in production images
// captured at any other resolution, even though the grid position math is already
// resolution-independent via normalized coordinates.
Bubble matchBubble(const Mat& thresh, Point2f expected, double searchRadius, const BubbleShapeProfile& shape,
                    double areaScale, bool& matched) {
    matched = false;
    Bubble result;
    double scaledAvgArea = shape.avgArea * areaScale;
    auto stats = sampleNearestContour(thresh, expected, searchRadius, false);
    if (stats.found) {
        bool areaOk = stats.area >= scaledAvgArea * shape.areaTolLow && stats.area <= scaledAvgArea * shape.areaTolHigh;
        bool aspectOk = fabs(stats.aspect - shape.avgAspect) <= shape.aspectTol;
        bool extentOk = stats.extent >= shape.avgExtent * shape.extentTolLow;
        if (areaOk && aspectOk && extentOk) {
            matched = true;
            result.bbox = stats.bbox;
            result.center = stats.center;
        }
    }
    if (!matched) {
        int diam = max(8, (int)round(sqrt(max(1.0, scaledAvgArea))));
        result.bbox = Rect((int)(expected.x - diam / 2.0), (int)(expected.y - diam / 2.0), diam, diam);
        result.center = expected;
    }
    return result;
}

// rejected takes priority over marked/matched in the overlay color: it's the bubble
// that had the strongest ink but whose SHAPE disqualified it (an X, checkmark, slash,
// circled-not-filled bubble, ...) — orange, distinct from green (accepted answer),
// blue (matched but unmarked), and red (no contour matched at all).
void drawBubbleOverlay(Mat& output, const Bubble& b, bool matched, bool marked, bool rejected = false) {
    Scalar color = rejected ? Scalar(0, 165, 255)
                 : marked   ? Scalar(0, 255, 0)
                 : matched  ? Scalar(255, 0, 0)
                            : Scalar(0, 0, 255);
    circle(output, Point(b.center), max(8, b.bbox.width / 2), color, 2);
}

// An unmatched cell falls back to a phantom sized/centered from the block-wide
// average and the raw 2-click interpolation — fine when nothing nearby is known, but
// if the SAME column has real matched bubbles immediately above and/or below the gap,
// those are strictly better local evidence: real paper/lens distortion means the true
// local spacing can drift slightly from the global straight-line interpolation, and a
// real neighbor's actual size is a better estimate than the block-wide average. When
// neighbors exist on both sides, interpolate the position between them (proportional
// to row distance) and average their size; with only one side, borrow just its size.
// This also directly improves fill-ratio accuracy for marks that dodge the shape
// gate (an oversized, fully-inked bubble reads as an outlier and falls back here) —
// a better-fitted sampling region catches the mark that an undersized generic phantom
// would under-sample.
void refineUnmatchedWithNeighbors(vector<vector<Bubble>>& grid, const vector<vector<bool>>& matchedFlags) {
    int rows = (int)grid.size();
    if (rows == 0) return;
    int cols = (int)grid[0].size();
    for (int c = 0; c < cols; c++) {
        for (int r = 0; r < rows; r++) {
            if (matchedFlags[r][c]) continue;
            int rAbove = -1;
            for (int rr = r - 1; rr >= 0; rr--) if (matchedFlags[rr][c]) { rAbove = rr; break; }
            int rBelow = -1;
            for (int rr = r + 1; rr < rows; rr++) if (matchedFlags[rr][c]) { rBelow = rr; break; }

            if (rAbove != -1 && rBelow != -1) {
                double t = (double)(r - rAbove) / (double)(rBelow - rAbove);
                const Bubble& above = grid[rAbove][c];
                const Bubble& below = grid[rBelow][c];
                Point2f center(above.center.x + (below.center.x - above.center.x) * (float)t,
                                above.center.y + (below.center.y - above.center.y) * (float)t);
                int w = (above.bbox.width + below.bbox.width) / 2;
                int h = (above.bbox.height + below.bbox.height) / 2;
                grid[r][c].center = center;
                grid[r][c].bbox = Rect((int)(center.x - w / 2.0), (int)(center.y - h / 2.0), w, h);
            } else if (rAbove != -1 || rBelow != -1) {
                const Bubble& neighbor = (rAbove != -1) ? grid[rAbove][c] : grid[rBelow][c];
                grid[r][c].bbox = Rect((int)(grid[r][c].center.x - neighbor.bbox.width / 2.0),
                                        (int)(grid[r][c].center.y - neighbor.bbox.height / 2.0),
                                        neighbor.bbox.width, neighbor.bbox.height);
            }
            // else: no matched neighbor anywhere in this column — leave the original
            // block-average-sized phantom in place, there's nothing better to go on.
        }
    }
}

// Classifies a single-choice column (LETTER or one DIGIT column). Mirrors
// pickMarkedOption's contention rules exactly (see its comment for the rationale): a
// non-clean-shaped mark is excluded from contention BEFORE comparing fills, so it can
// neither win outright nor falsely tie a real mark into "ambiguous".
//
// Return codes: >=0 the winning row, -2 blank, -3 ambiguous (two or more CLEAN rows
// marked with comparably strong fill), -4 rejected — every row that crossed threshold
// has non-bubble-shaped ink, so intent is refused rather than guessed.
int classifySingleChoiceColumn(const Mat& thresh, const Mat& gray, Mat& output, GridBlockSpec* spec,
                                double searchRadius, double areaScale, const vector<vector<Point2f>>& grid) {
    vector<vector<Bubble>> bubbles(spec->rows, vector<Bubble>(1));
    vector<vector<bool>> matchedFlags(spec->rows, vector<bool>(1));

    Point2f curr = grid[0][0];
    for (int r = 0; r < spec->rows; r++) {
        bool matched;
        bubbles[r][0] = matchBubble(thresh, curr, searchRadius, spec->shape, areaScale, matched);
        matchedFlags[r][0] = matched;

        if (r + 1 < spec->rows) {
            Point2f step = grid[r+1][0] - grid[r][0];
            if (matched) curr = bubbles[r][0].center + step;
            else curr = curr + step;
        }
    }
    refineUnmatchedWithNeighbors(bubbles, matchedFlags);

    vector<MarkAnalysis> marks(spec->rows);
    double topFill = -1; int topR = -1;
    bool anyAboveThreshold = false;
    int bestR = -1, secondR = -1;
    double best = -1, second = -1;
    vector<int> candidateRows;
    for (int r = 0; r < spec->rows; r++) {
        marks[r] = analyzeMark(gray, bubbles[r][0]);
        double f = marks[r].fillRatio;
        if (f > topFill) { topFill = f; topR = r; }
        if (f < spec->markedThresholdAt(r, 0)) continue;
        anyAboveThreshold = true;
        if (!isCleanFilledMark(marks[r])) continue;
        candidateRows.push_back(r);
        if (f > best) { second = best; secondR = bestR; best = f; bestR = r; }
        else if (f > second) { second = f; secondR = r; }
    }

    int result;
    if (bestR < 0) result = anyAboveThreshold ? -4 : -2;
    // Kept in sync with pickMarkedOption's MIN_MARGIN — see its comment for why 0.30.
    else if (secondR >= 0 && best - second < 0.30) result = -3;
    else result = bestR;

    for (int r = 0; r < spec->rows; r++) {
        // Ambiguous highlights EVERY contending clean mark (not just one) so the
        // overlay actually shows there were multiple — a single green ring here would
        // otherwise look identical to a normal, unambiguous pick.
        bool marked = (result >= 0 && r == result) ||
                      (result == -3 && find(candidateRows.begin(), candidateRows.end(), r) != candidateRows.end());
        bool rejected = (result == -4 && r == topR);
        drawBubbleOverlay(output, bubbles[r][0], matchedFlags[r][0], marked, rejected);
    }
    return result;
}

// Everything runProduction computes, in a form the FFI layer (bs_run) can serialize
// directly without re-deriving it from the YAML file/cout lines this function also
// still produces for CLI use. Field meanings mirror the YAML written to outJsonPath
// exactly (letter/digits/answers/warnings) plus the registration diagnostics that were
// previously only ever printed to cout.
struct ProductionResult {
    string letter = "blank";
    vector<string> digits;
    vector<string> answers;
    vector<string> warnings;
    bool registered = false;
    int matchedBoxes = 0;
    bool deskewed = false;
};

// result/errorOut are optional (nullptr for CLI use, which keeps relying on outJsonPath/
// cout as before): the FFI wrapper (bs_run) passes both so it can return the full result
// as JSON without requiring a round-trip through a file on disk.
bool runProduction(const string& imagePath, const string& profilePath, const string& outJsonPath, const string& debugImagePath,
                   ProductionResult* result = nullptr, string* errorOut = nullptr, string* errorCodeOut = nullptr) {
    CalibrationProfile profile;
    string profileError, profileErrorCode;
    if (!loadProfile(profile, profilePath, &profileError, &profileErrorCode)) {
        cerr << profileError << endl;
        if (errorOut) *errorOut = profileError;
        if (errorCodeOut) *errorCodeOut = profileErrorCode;
        return false;
    }

    Mat raw = imread(imagePath);
    if (raw.empty()) {
        cerr << "Could not read image: " << imagePath << endl;
        if (errorOut) *errorOut = "Could not read image: " + imagePath;
        if (errorCodeOut) *errorCodeOut = "IMAGE_UNREADABLE";
        return false;
    }

    bool deskewed = false;
    int matchedBoxes = 0;
    vector<Point2f> corners = findCornerSquares(raw);
    if (corners.size() == 4) {
        matchedBoxes = 4;
        vector<Point2f> dst = {
            Point2f(0, 0), Point2f(1000, 0),
            Point2f(1000, 1414), Point2f(0, 1414)
        };
        Mat H = getPerspectiveTransform(corners, dst);
        warpPerspective(raw, raw, H, Size(1000, 1414));
        deskewed = true;
        cout << "Registration: Auto-detected 4 corner squares -> Deskewed to 1000x1414 canvas." << endl;
    } else {
        cerr << "Registration Error: Could not detect the 4 corner squares." << endl;
        if (errorOut) *errorOut = "Registration Error: Could not detect the 4 corner squares.";
        if (errorCodeOut) *errorCodeOut = "REGISTRATION_FAILED";
        return false;
    }

    Mat thresh, gray;
    preprocess(raw, thresh, gray);
    Mat output = raw.clone();

    map<string, GridBlockSpec*> blockByName;
    for (auto& b : profile.blocks) blockByName[b.name] = &b;

    string letterResult = "blank";
    vector<string> digitResults(profile.idDigitColumns, "blank");
    vector<string> answers(profile.numQuestions, "blank");
    vector<string> warnings;

    if (profile.idLetterCount > 0 && blockByName.count("letter")) {
        auto* spec = blockByName["letter"];
        Point2d tl(spec->tlNorm.x * profile.calibWidth, spec->tlNorm.y * profile.calibHeight);
        Point2d br(spec->brNorm.x * profile.calibWidth, spec->brNorm.y * profile.calibHeight);
        auto grid = interpolateGrid(tl, br, spec->rows, 1);
        double searchRadius = computeSearchRadius(grid, spec->rows, 1);
        int bestR = classifySingleChoiceColumn(thresh, gray, output, spec, searchRadius, 1.0, grid);
        if (bestR == -4) {
            letterResult = "rejected";
            warnings.push_back("LETTER: the marked bubble doesn't look like a filled-in bubble "
                                "(looks like an X, checkmark, slash, or circled-not-filled mark) — flagged for manual review.");
        } else if (bestR == -3) {
            letterResult = "multiple_marks";
            warnings.push_back("LETTER: two or more bubbles are clearly filled — flagged for manual review instead of guessing.");
        } else {
            letterResult = (bestR >= 0 && bestR < (int)profile.idLetterLabels.size()) ? profile.idLetterLabels[bestR] : "blank";
        }
    }

    for (int i = 1; i <= profile.idDigitColumns; i++) {
        string name = "digit_" + to_string(i);
        if (!blockByName.count(name)) continue;
        auto* spec = blockByName[name];
        Point2d tl(spec->tlNorm.x * profile.calibWidth, spec->tlNorm.y * profile.calibHeight);
        Point2d br(spec->brNorm.x * profile.calibWidth, spec->brNorm.y * profile.calibHeight);
        auto grid = interpolateGrid(tl, br, spec->rows, 1);
        double searchRadius = computeSearchRadius(grid, spec->rows, 1);
        int bestR = classifySingleChoiceColumn(thresh, gray, output, spec, searchRadius, 1.0, grid);
        if (bestR == -4) {
            digitResults[i - 1] = "rejected";
            warnings.push_back(name + ": the marked bubble doesn't look like a filled-in bubble "
                                "(looks like an X, checkmark, slash, or circled-not-filled mark) — flagged for manual review.");
        } else if (bestR == -3) {
            digitResults[i - 1] = "multiple_marks";
            warnings.push_back(name + ": two or more bubbles are clearly filled — flagged for manual review instead of guessing.");
        } else {
            digitResults[i - 1] = (bestR >= 0) ? to_string(bestR) : "blank";
        }
    }

    int numBlocks = (int)ceil((double)profile.numQuestions / profile.questionsPerBlock);
    int qOffset = 0;
    for (int bIdx = 1; bIdx <= numBlocks; bIdx++) {
        string name = "answers_" + to_string(bIdx);
        int rowsInThisBlock = min(profile.questionsPerBlock, profile.numQuestions - qOffset);
        if (!blockByName.count(name)) { qOffset += rowsInThisBlock; continue; }
        auto* spec = blockByName[name];
        Point2d tl(spec->tlNorm.x * profile.calibWidth, spec->tlNorm.y * profile.calibHeight);
        Point2d br(spec->brNorm.x * profile.calibWidth, spec->brNorm.y * profile.calibHeight);
        auto grid = interpolateGrid(tl, br, spec->rows, spec->cols);
        double searchRadius = computeSearchRadius(grid, spec->rows, spec->cols);

        vector<vector<Bubble>> optionsGrid(spec->rows, vector<Bubble>(spec->cols));
        vector<vector<bool>> matchedGrid(spec->rows, vector<bool>(spec->cols));
        vector<Point2f> colStarts(spec->cols);
        for (int c = 0; c < spec->cols; c++) colStarts[c] = grid[0][c];

        for (int c = 0; c < spec->cols; c++) {
            Point2f curr = colStarts[c];
            for (int r = 0; r < spec->rows; r++) {
                bool matched;
                optionsGrid[r][c] = matchBubble(thresh, curr, searchRadius, spec->shape, 1.0, matched);
                matchedGrid[r][c] = matched;

                if (r == 0 && matched && c + 1 < spec->cols) {
                    colStarts[c + 1] = optionsGrid[0][c].center + (grid[0][c + 1] - grid[0][c]);
                }

                if (r + 1 < spec->rows) {
                    Point2f step = grid[r+1][c] - grid[r][c];
                    if (matched) curr = optionsGrid[r][c].center + step;
                    else curr = curr + step;
                }
            }
        }
        refineUnmatchedWithNeighbors(optionsGrid, matchedGrid);

        for (int r = 0; r < spec->rows; r++) {
            int qNum = qOffset + r + 1;
            vector<Bubble>& options = optionsGrid[r];
            vector<double> minFillThresholds(spec->cols);
            int matchedCount = 0;
            for (int c = 0; c < spec->cols; c++) {
                minFillThresholds[c] = spec->markedThresholdAt(r, c);
                if (matchedGrid[r][c]) matchedCount++;
            }
            if (matchedCount < spec->cols) {
                warnings.push_back("Q" + to_string(qNum) + ": only " + to_string(matchedCount) + "/" +
                                    to_string(spec->cols) + " choice bubbles matched the calibration profile — check alignment or ink quality.");
            }

            int topIdx = -1;
            vector<int> candidateIdxs;
            int picked = pickMarkedOption(gray, options, minFillThresholds, topIdx, &candidateIdxs);
            string ans = "blank";
            if (picked == -3) {
                ans = "multiple_marks";
                string labels;
                for (int idx : candidateIdxs) { if (!labels.empty()) labels += "/"; labels += char('A' + idx); }
                warnings.push_back("Q" + to_string(qNum) + ": more than one bubble is clearly filled (" + labels +
                                    ") — flagged for manual review instead of guessing.");
            } else if (picked == -4) {
                ans = "rejected";
                string label = (topIdx >= 0 && topIdx < spec->cols) ? string(1, char('A' + topIdx)) : "?";
                warnings.push_back("Q" + to_string(qNum) + ": marked option " + label +
                                    " doesn't look like a filled-in bubble (looks like an X, checkmark, slash, "
                                    "or circled-not-filled mark) — flagged for manual review.");
            } else if (picked >= 0 && picked < spec->cols) {
                ans = string(1, char('A' + picked));
            }
            answers[qNum - 1] = ans;

            for (int c = 0; c < spec->cols; c++) {
                // Ambiguous highlights EVERY contending clean mark, not just one — a
                // single green ring would otherwise look identical to a normal pick.
                bool marked = (int)c == picked ||
                               (picked == -3 && find(candidateIdxs.begin(), candidateIdxs.end(), (int)c) != candidateIdxs.end());
                drawBubbleOverlay(output, options[c], matchedGrid[r][c], marked, picked == -4 && (int)c == topIdx);
            }
        }
        qOffset += spec->rows;
    }

    cout << "LETTER = " << letterResult << endl;
    for (int i = 0; i < profile.idDigitColumns; i++) cout << "DIGIT" << (i + 1) << " = " << digitResults[i] << endl;
    cout << endl;
    for (int i = 0; i < profile.numQuestions; i++) cout << "Q" << (i + 1) << ": " << answers[i] << endl;
    if (!warnings.empty()) {
        cout << endl << "=== WARNINGS ===" << endl;
        for (auto& w : warnings) cout << w << endl;
    }

    if (!outJsonPath.empty()) {
        FileStorage fs(outJsonPath, FileStorage::WRITE);
        fs << "letter" << letterResult;
        fs << "digits" << "[";
        for (auto& d : digitResults) fs << d;
        fs << "]";
        fs << "answers" << "[";
        for (auto& a : answers) fs << a;
        fs << "]";
        fs << "warnings" << "[";
        for (auto& w : warnings) fs << w;
        fs << "]";
        fs.release();
    }

    if (!debugImagePath.empty()) imwrite(debugImagePath, output);

    if (result) {
        result->letter = letterResult;
        result->digits = digitResults;
        result->answers = answers;
        result->warnings = warnings;
        result->registered = matchedBoxes > 0;
        result->matchedBoxes = matchedBoxes;
        result->deskewed = deskewed;
    }

    return true;
}

// =============================================================================
// FFI API (FOR FLUTTER / DART INTEROP) — IMPLEMENTATION
// =============================================================================
// The contract itself (function-by-function doc comments, the calibration_request
// schema, the bs_run JSON result shape) lives in omr_engine/ffi_api.h, included near
// the top of this file — this section is only the bodies.

namespace ffi_detail {

string jsonEscape(const string& s) {
    string out;
    out.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) { char buf[8]; snprintf(buf, sizeof buf, "\\u%04x", c); out += buf; }
                else out += (char)c;
        }
    }
    return out;
}

string jsonStr(const string& s) { return "\"" + jsonEscape(s) + "\""; }

string jsonStrArray(const vector<string>& v) {
    string out = "[";
    for (size_t i = 0; i < v.size(); i++) { if (i) out += ","; out += jsonStr(v[i]); }
    out += "]";
    return out;
}

// Heap-allocates a copy of s with malloc() so it can safely cross the FFI boundary and
// outlive this function's stack frame — the caller (Dart, via bs_free_string) owns it
// from this point on.
char* dupString(const string& s) {
    char* p = (char*)malloc(s.size() + 1);
    if (p) memcpy(p, s.c_str(), s.size() + 1);
    return p;
}

} // namespace ffi_detail

BS_API const char* bs_calibrate(const char* requestPath) {
    using namespace ffi_detail;
    if (!requestPath) {
        return dupString("{\"success\":false,\"error\":" + jsonStr("requestPath is null") + ",\"log\":[]}");
    }
    CalibrationRequest req;
    if (!loadCalibrationRequest(req, requestPath)) {
        return dupString("{\"success\":false,\"error\":" +
                          jsonStr("Failed to load calibration request: " + string(requestPath)) + ",\"log\":[]}");
    }
    string err;
    vector<string> log;
    double fiducialRatio = 1.414;
    bool ok = runCalibration(req, err, &log, &fiducialRatio);

    ostringstream out;
    out << "{\"success\":" << (ok ? "true" : "false")
        << ",\"error\":" << jsonStr(err)
        << ",\"fiducial_ratio\":" << fiducialRatio
        << ",\"log\":" << jsonStrArray(log) << "}";
    return dupString(out.str());
}

BS_API const char* bs_run(const char* imagePath, const char* profilePath, const char* debugImagePath) {
    using namespace ffi_detail;
    ProductionResult r;
    string err, errorCode;
    bool ok = false;
    // Deliberately falls through to the SAME JSON-building code below on every path
    // (null args, a failed runProduction, or a clean run) rather than early-returning a
    // differently-shaped object for the null-arg case — a caller should never have to
    // special-case one failure mode's JSON shape vs. another's; "success": false always
    // comes with the same fields (letter/digits/answers/warnings/registration), just at
    // ProductionResult's default values (r.digits/r.answers are empty arrays here, NOT
    // sized to the profile's questions/digit-columns, since the profile was never even
    // read — always check "success" before trusting array lengths).
    if (!imagePath || !profilePath) {
        err = "imagePath/profilePath is null";
        errorCode = "INVALID_ARGS";
    } else {
        ok = runProduction(imagePath, profilePath, "", debugImagePath ? debugImagePath : "", &r, &err, &errorCode);
    }

    ostringstream out;
    out << "{\"success\":" << (ok ? "true" : "false")
        << ",\"error\":" << jsonStr(err)
        << ",\"error_code\":" << jsonStr(errorCode)
        << ",\"letter\":" << jsonStr(r.letter)
        << ",\"digits\":" << jsonStrArray(r.digits)
        << ",\"answers\":" << jsonStrArray(r.answers)
        << ",\"warnings\":" << jsonStrArray(r.warnings)
        << ",\"registration\":{"
        << "\"matched\":" << (r.registered ? "true" : "false")
        << ",\"matched_boxes\":" << r.matchedBoxes
        << ",\"deskewed\":" << (r.deskewed ? "true" : "false")
        << "}}";
    return dupString(out.str());
}

BS_API int bs_profile_status(const char* profilePath) {
    if (!profilePath) return 0;
    CalibrationProfile p;
    if (!loadProfile(p, profilePath)) return 0;
    return p.blocks.empty() ? 0 : 1;
}

BS_API void bs_free_string(const char* ptr) {
    free((void*)ptr);
}

// =============================================================================
// MAIN
// =============================================================================
int main(int argc, char** argv) {
    if (argc < 2) {
        cerr << "Usage:\n"
             << "  bubble_sheet status <profile.json>\n"
             << "  bubble_sheet calibrate <calibration_request.json>\n"
             << "  bubble_sheet run <image> <profile.json> [out.json] [debug.png]\n";
        return 1;
    }

    string mode = argv[1];

    if (mode == "status") {
        if (argc < 3) { cerr << "Usage: bubble_sheet status <profile.json>" << endl; return 1; }
        ifstream f(argv[2]);
        bool exists = f.good();
        cout << (exists ? "EXISTS" : "MISSING") << endl;
        return exists ? 0 : 1;
    }

    if (mode == "calibrate") {
        if (argc < 3) { cerr << "Usage: bubble_sheet calibrate <calibration_request.json>" << endl; return 1; }
        CalibrationRequest req;
        if (!loadCalibrationRequest(req, argv[2])) { cerr << "Failed to load calibration request: " << argv[2] << endl; return 1; }
        string err;
        if (!runCalibration(req, err)) { cerr << "Calibration failed: " << err << endl; return 1; }
        cout << "Profile saved to " << req.profilePath << endl;
        return 0;
    }

    if (mode == "run") {
        if (argc < 4) { cerr << "Usage: bubble_sheet run <image> <profile.json> [out.json] [debug.png]" << endl; return 1; }
        string imagePath = argv[2];
        string profilePath = argv[3];
        string outPath = (argc >= 5) ? argv[4] : "";
        string debugPath = (argc >= 6) ? argv[5] : "";
        return runProduction(imagePath, profilePath, outPath, debugPath) ? 0 : 1;
    }

    cerr << "Unknown mode: " << mode << endl;
    return 1;
}