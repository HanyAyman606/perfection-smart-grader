#pragma once

#include <vector>
#include "result.h"

namespace grouping {

std::vector<std::vector<Detection>> group_by_rows(const std::vector<Detection>& detections, int tolerance);

std::vector<std::vector<Detection>> group_by_columns(const std::vector<Detection>& detections, int tolerance);

} // namespace grouping
