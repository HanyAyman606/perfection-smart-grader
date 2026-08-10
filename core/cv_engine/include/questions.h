#pragma once

#include <vector>
#include <string>
#include <tuple>
#include <optional>
#include "result.h"

namespace questions {

std::vector<QuestionResult> process_questions(
    const std::vector<Detection>& detections,
    int num_questions,
    int num_question_columns,
    const std::vector<std::string>& choice_labels,
    int row_tolerance_px,
    const std::vector<int>& mcq_column_sizes = {}
);

std::tuple<std::vector<IDColumnResult>, std::optional<std::string>, std::optional<std::string>, bool> process_student_id(
    const std::vector<Detection>& detections,
    int num_digits,
    int num_letters,
    const std::vector<std::string>& letters,
    const std::vector<std::string>& column_labels,
    int row_tolerance_px
);

} // namespace questions
