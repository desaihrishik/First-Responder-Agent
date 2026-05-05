#pragma once

#include <cstdint>
#include <cuda_runtime.h>

namespace dispatch {

struct FilterParams {
    int8_t target_value;        // for int8 equality filter
    float lat_min, lat_max;     // bounding box
    float lon_min, lon_max;
    int64_t num_rows;
};

struct FilterResult {
    uint32_t* bitmask;          // device pointer: 1 bit per row
    int64_t num_rows;
    int64_t match_count;
    float kernel_ms;
};

// Filter int8 column for equality (complaint_type or borough enum)
FilterResult run_filter_int8(
    const int8_t* d_column,
    FilterParams params,
    cudaStream_t stream = 0
);

// Filter float32 column pair for lat/lon bounding box
FilterResult run_filter_bbox(
    const float* d_lat,
    const float* d_lon,
    FilterParams params,
    cudaStream_t stream = 0
);

// Combined filter: int8 match AND bounding box
FilterResult run_filter_combined(
    const int8_t* d_type_col,
    const float* d_lat,
    const float* d_lon,
    FilterParams params,
    cudaStream_t stream = 0
);

// Count set bits in bitmask (device-side reduction)
int64_t count_matches(const uint32_t* d_bitmask, int64_t num_rows, cudaStream_t stream = 0);

// Free filter result bitmask
void free_filter_result(FilterResult& result);

// Setup unified memory hints for Arrow buffers
void setup_unified_memory(void* ptr, size_t size, int gpu_id = 0);

} // namespace dispatch
