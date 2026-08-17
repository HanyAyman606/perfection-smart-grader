#pragma once

#include <vector>
#include <string>
#include <map>
#include <tuple>
#include <optional>
#include "result.h"

namespace questions {

// Legacy/generic process_questions (backward compatible)
std::vector<QuestionResult> process_questions(
    const std::vector<Detection>& detections,
    int num_questions,
    int num_question_columns,
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px,
    int column_capacity = 0 // 0 = even split across columns (default, existing
                             // behavior for any generic/non-shamel layout).
                             // >0 = fixed max questions per column, filled
                             // front-to-back (shamel: 20 = two printed 10-row
                             // blocks). See process_questions definition in
                             // questions.cpp for the full rationale - must stay
                             // consistent with shamel_phases::validate_mcq_regions'
                             // expected-distribution rule for shamel mode.
);

// Quiz mode: per-column question counts
std::vector<QuestionResult> process_questions_per_column(
    const std::vector<Detection>& detections,
    int num_question_columns,
    const std::map<std::string, int>& per_column_questions,  // column_idx as string ("0", "1", ...) -> question_count
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px
);

std::tuple<std::vector<IDColumnResult>, std::optional<std::string>, std::optional<std::string>> process_student_id(
    const std::vector<Detection>& detections,
    int num_digits,
    int num_letters,
    const std::vector<std::string>& letters,
    const std::vector<std::string>& column_labels,
    int row_tolerance_px
);

} // namespace questions