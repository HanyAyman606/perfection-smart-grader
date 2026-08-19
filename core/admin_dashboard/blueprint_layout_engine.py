"""
blueprint_layout_engine.py
----------------------------
Pure MCQ bubble-column layout math, extracted out of ProjectManager.

Why this is its own module: ProjectManager's job (per its own docstring)
is "workspace lifecycle + JSON config I/O" — but compute_mcq_column_layout
and compute_mcq_column_layout_fixed are neither of those things. They're
pure functions (same input always produces the same output, no file
access, no side effects) mixed onto a class whose reason to change is
"the on-disk project format changed." That mixing meant you couldn't
unit-test the column-split math without a ProjectManager instance, and a
change to persistence code sat in the same file/diff as a change to
layout math with nothing keeping them apart.

Both algorithms are kept here side by side (rather than as one
parameterized function) because they encode genuinely different layout
strategies — see each docstring — not because of duplication that should
be collapsed. Renaming or removing either one is a decision about layout
policy, not something to do casually alongside a persistence change.
"""

MCQ_LAYOUT_COLS = 3  # bubble sheet studio always spreads MCQs across 3 columns


class BlueprintLayoutEngine:
    """Stateless — every method is a @staticmethod. No project_dir, no
    disk access, safe to call from anywhere (including unit tests) with
    just the numbers involved."""

    @staticmethod
    def compute_even_split(mcq_count: int, num_cols: int = MCQ_LAYOUT_COLS) -> dict:
        """Mirrors the Bubble Sheet Studio's column-split math exactly
        (col1 = ceil(n/3), col2 = ceil(remaining/2), col3 = whatever is
        left), generalized to any column count: each column takes the
        ceiling of the questions still remaining divided by the columns
        still left, so the studio's 3-column formula falls out as the
        default case. Returns e.g. {"num_cols": 3, "columns": {"1": 9,
        "2": 8, "3": 8}} for 25 questions."""
        remaining = max(0, mcq_count)
        cols_left = max(1, num_cols)
        columns = {}
        for i in range(1, num_cols + 1):
            n = -(-remaining // cols_left)  # ceil division
            columns[str(i)] = n
            remaining -= n
            cols_left -= 1
        return {"num_cols": num_cols, "columns": columns}

    @staticmethod
    def compute_fixed_fill(mcq_count: int, num_cols: int = 6, col_size: int = 10) -> dict:
        """Shamel mode's column layout: fill each column to `col_size`
        questions before starting the next one, left to right — NOT an
        even split like compute_even_split(). For 44 questions across 6
        columns of 10: {"1": 10, "2": 10, "3": 10, "4": 10, "5": 4,
        "6": 0}. Any column beyond what's needed is explicitly 0, not
        omitted, so the mobile client can always expect exactly
        `num_cols` keys."""
        remaining = max(0, mcq_count)
        columns = {}
        for i in range(1, num_cols + 1):
            n = min(col_size, remaining)
            columns[str(i)] = n
            remaining -= n
        return {"num_cols": num_cols, "columns": columns}