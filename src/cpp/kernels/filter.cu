/**
 * CUDA filter kernels for NYC Dispatch Engine.
 * Designed for NVIDIA Blackwell (sm_90) on GB10 Grace Blackwell Superchip.
 * Uses warp ballot for efficient bitmask generation.
 */

#include "filter.cuh"
#include <cstdio>

namespace dispatch {

// ============================================================================
// Kernel: filter int8 column for equality
// Coalesced reads, __ballot_sync warp ballot, single write per warp to bitmask
// ============================================================================
__global__ void filter_int8_kernel(
    const int8_t* __restrict__ column,
    uint32_t* __restrict__ bitmask,
    int8_t target,
    int64_t num_rows
) {
    int64_t tid = static_cast<int64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    int lane = threadIdx.x & 31;  // lane within warp
    int warp_id = tid >> 5;       // global warp index

    bool match = false;
    if (tid < num_rows) {
        match = (column[tid] == target);
    }

    // Warp ballot: each lane contributes 1 bit
    unsigned ballot = __ballot_sync(0xFFFFFFFF, match);

    // Lane 0 of each warp writes the 32-bit bitmask word
    if (lane == 0) {
        int64_t bitmask_idx = warp_id;
        int64_t bitmask_size = (num_rows + 31) / 32;
        if (bitmask_idx < bitmask_size) {
            bitmask[bitmask_idx] = ballot;
        }
    }
}

// ============================================================================
// Kernel: filter float32 lat/lon bounding box
// ============================================================================
__global__ void filter_bbox_kernel(
    const float* __restrict__ lat,
    const float* __restrict__ lon,
    uint32_t* __restrict__ bitmask,
    float lat_min, float lat_max,
    float lon_min, float lon_max,
    int64_t num_rows
) {
    int64_t tid = static_cast<int64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    int lane = threadIdx.x & 31;
    int warp_id = tid >> 5;

    bool match = false;
    if (tid < num_rows) {
        float la = lat[tid];
        float lo = lon[tid];
        match = (la >= lat_min && la <= lat_max && lo >= lon_min && lo <= lon_max);
    }

    unsigned ballot = __ballot_sync(0xFFFFFFFF, match);

    if (lane == 0) {
        int64_t bitmask_idx = warp_id;
        int64_t bitmask_size = (num_rows + 31) / 32;
        if (bitmask_idx < bitmask_size) {
            bitmask[bitmask_idx] = ballot;
        }
    }
}

// ============================================================================
// Kernel: combined int8 match AND bounding box
// ============================================================================
__global__ void filter_combined_kernel(
    const int8_t* __restrict__ type_col,
    const float* __restrict__ lat,
    const float* __restrict__ lon,
    uint32_t* __restrict__ bitmask,
    int8_t target_type,
    float lat_min, float lat_max,
    float lon_min, float lon_max,
    int64_t num_rows
) {
    int64_t tid = static_cast<int64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    int lane = threadIdx.x & 31;
    int warp_id = tid >> 5;

    bool match = false;
    if (tid < num_rows) {
        bool type_match = (type_col[tid] == target_type);
        float la = lat[tid];
        float lo = lon[tid];
        bool bbox_match = (la >= lat_min && la <= lat_max && lo >= lon_min && lo <= lon_max);
        match = type_match && bbox_match;
    }

    unsigned ballot = __ballot_sync(0xFFFFFFFF, match);

    if (lane == 0) {
        int64_t bitmask_idx = warp_id;
        int64_t bitmask_size = (num_rows + 31) / 32;
        if (bitmask_idx < bitmask_size) {
            bitmask[bitmask_idx] = ballot;
        }
    }
}

// ============================================================================
// Kernel: popcount reduction for match counting
// ============================================================================
__global__ void popcount_kernel(
    const uint32_t* __restrict__ bitmask,
    int64_t* __restrict__ count,
    int64_t num_words
) {
    extern __shared__ int64_t sdata[];

    int64_t tid = static_cast<int64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    int64_t local_count = 0;

    if (tid < num_words) {
        local_count = __popc(bitmask[tid]);
    }

    sdata[threadIdx.x] = local_count;
    __syncthreads();

    // Block-level reduction
    for (int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (threadIdx.x < s) {
            sdata[threadIdx.x] += sdata[threadIdx.x + s];
        }
        __syncthreads();
    }

    if (threadIdx.x == 0) {
        atomicAdd(count, sdata[0]);
    }
}

// ============================================================================
// Host wrapper functions
// ============================================================================

static uint32_t* allocate_bitmask(int64_t num_rows) {
    int64_t num_words = (num_rows + 31) / 32;
    uint32_t* d_bitmask;
    cudaMalloc(&d_bitmask, num_words * sizeof(uint32_t));
    cudaMemset(d_bitmask, 0, num_words * sizeof(uint32_t));
    return d_bitmask;
}

FilterResult run_filter_int8(
    const int8_t* d_column,
    FilterParams params,
    cudaStream_t stream
) {
    FilterResult result = {};
    result.num_rows = params.num_rows;
    result.bitmask = allocate_bitmask(params.num_rows);

    int block_size = 256;
    int grid_size = static_cast<int>((params.num_rows + block_size - 1) / block_size);

    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);
    cudaEventRecord(start, stream);

    filter_int8_kernel<<<grid_size, block_size, 0, stream>>>(
        d_column, result.bitmask, params.target_value, params.num_rows
    );

    cudaEventRecord(stop, stream);
    cudaEventSynchronize(stop);
    cudaEventElapsedTime(&result.kernel_ms, start, stop);

    result.match_count = count_matches(result.bitmask, params.num_rows, stream);

    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return result;
}

FilterResult run_filter_bbox(
    const float* d_lat,
    const float* d_lon,
    FilterParams params,
    cudaStream_t stream
) {
    FilterResult result = {};
    result.num_rows = params.num_rows;
    result.bitmask = allocate_bitmask(params.num_rows);

    int block_size = 256;
    int grid_size = static_cast<int>((params.num_rows + block_size - 1) / block_size);

    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);
    cudaEventRecord(start, stream);

    filter_bbox_kernel<<<grid_size, block_size, 0, stream>>>(
        d_lat, d_lon, result.bitmask,
        params.lat_min, params.lat_max,
        params.lon_min, params.lon_max,
        params.num_rows
    );

    cudaEventRecord(stop, stream);
    cudaEventSynchronize(stop);
    cudaEventElapsedTime(&result.kernel_ms, start, stop);

    result.match_count = count_matches(result.bitmask, params.num_rows, stream);

    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return result;
}

FilterResult run_filter_combined(
    const int8_t* d_type_col,
    const float* d_lat,
    const float* d_lon,
    FilterParams params,
    cudaStream_t stream
) {
    FilterResult result = {};
    result.num_rows = params.num_rows;
    result.bitmask = allocate_bitmask(params.num_rows);

    int block_size = 256;
    int grid_size = static_cast<int>((params.num_rows + block_size - 1) / block_size);

    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);
    cudaEventRecord(start, stream);

    filter_combined_kernel<<<grid_size, block_size, 0, stream>>>(
        d_type_col, d_lat, d_lon, result.bitmask,
        params.target_value,
        params.lat_min, params.lat_max,
        params.lon_min, params.lon_max,
        params.num_rows
    );

    cudaEventRecord(stop, stream);
    cudaEventSynchronize(stop);
    cudaEventElapsedTime(&result.kernel_ms, start, stop);

    result.match_count = count_matches(result.bitmask, params.num_rows, stream);

    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return result;
}

int64_t count_matches(const uint32_t* d_bitmask, int64_t num_rows, cudaStream_t stream) {
    int64_t num_words = (num_rows + 31) / 32;

    int64_t* d_count;
    cudaMalloc(&d_count, sizeof(int64_t));
    cudaMemset(d_count, 0, sizeof(int64_t));

    int block_size = 256;
    int grid_size = static_cast<int>((num_words + block_size - 1) / block_size);

    popcount_kernel<<<grid_size, block_size, block_size * sizeof(int64_t), stream>>>(
        d_bitmask, d_count, num_words
    );

    int64_t h_count = 0;
    cudaMemcpy(&h_count, d_count, sizeof(int64_t), cudaMemcpyDeviceToHost);
    cudaFree(d_count);

    return h_count;
}

void free_filter_result(FilterResult& result) {
    if (result.bitmask) {
        cudaFree(result.bitmask);
        result.bitmask = nullptr;
    }
}

void setup_unified_memory(void* ptr, size_t size, int gpu_id) {
    cudaMemAdvise(ptr, size, cudaMemAdviseSetPreferredLocation, gpu_id);
    cudaMemAdvise(ptr, size, cudaMemAdviseSetAccessedBy, cudaCpuDeviceId);
    cudaMemPrefetchAsync(ptr, size, gpu_id, 0);
    printf("Unified memory setup: %zu bytes advised for GPU %d\n", size, gpu_id);
}

} // namespace dispatch
