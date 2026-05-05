/**
 * pybind11 bindings for the NYC Dispatch C++ engine.
 * Exposes: load_csv, search, benchmark
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <string>
#include <vector>
#include <map>
#include <chrono>
#include <algorithm>
#include <numeric>
#include <memory>
#include <iostream>

#include "../ingestion/csv_parser.h"
// Note: filter.cuh is only included when CUDA is available
#ifdef __CUDACC__
#include "../kernels/filter.cuh"
#endif

namespace py = pybind11;

namespace dispatch {

class EngineHandle {
public:
    std::shared_ptr<arrow::RecordBatch> batch;
    int64_t num_rows = 0;
    bool cuda_available = false;

    // Cached column pointers for fast access
    const int8_t* complaint_types = nullptr;
    const int8_t* boroughs = nullptr;
    const int64_t* dates = nullptr;
    const float* resolutions = nullptr;
    const float* lats = nullptr;
    const float* lons = nullptr;

    void cache_columns() {
        if (!batch) return;
        num_rows = batch->num_rows();

        auto ct_arr = std::static_pointer_cast<arrow::Int8Array>(batch->column(0));
        complaint_types = ct_arr->raw_values();

        auto br_arr = std::static_pointer_cast<arrow::Int8Array>(batch->column(1));
        boroughs = br_arr->raw_values();

        auto dt_arr = std::static_pointer_cast<arrow::Int64Array>(batch->column(2));
        dates = dt_arr->raw_values();

        auto res_arr = std::static_pointer_cast<arrow::FloatArray>(batch->column(3));
        resolutions = res_arr->raw_values();

        auto lat_arr = std::static_pointer_cast<arrow::FloatArray>(batch->column(4));
        lats = lat_arr->raw_values();

        auto lon_arr = std::static_pointer_cast<arrow::FloatArray>(batch->column(5));
        lons = lon_arr->raw_values();
    }
};

static std::shared_ptr<EngineHandle> g_handle;

py::dict load_csv(const std::string& path, int num_threads = 20) {
    CSVParser parser(num_threads);
    auto batch = parser.parse(path);

    if (!batch) {
        return py::dict(
            py::arg("status") = "error",
            py::arg("message") = "Failed to parse CSV"
        );
    }

    g_handle = std::make_shared<EngineHandle>();
    g_handle->batch = batch;
    g_handle->cache_columns();

    #ifdef __CUDACC__
    g_handle->cuda_available = true;
    #endif

    return py::dict(
        py::arg("status") = "ok",
        py::arg("rows") = g_handle->num_rows,
        py::arg("cuda") = g_handle->cuda_available
    );
}

py::dict load_ipc(const std::string& path) {
    CSVParser parser;
    auto batch = parser.read_ipc(path);

    if (!batch) {
        return py::dict(
            py::arg("status") = "error",
            py::arg("message") = "Failed to read IPC file"
        );
    }

    g_handle = std::make_shared<EngineHandle>();
    g_handle->batch = batch;
    g_handle->cache_columns();

    return py::dict(
        py::arg("status") = "ok",
        py::arg("rows") = g_handle->num_rows
    );
}

std::vector<py::dict> search(
    int borough_code,
    int complaint_code,
    float lat, float lon,
    int k
) {
    std::vector<py::dict> results;
    if (!g_handle || !g_handle->batch) return results;

    // CPU-based search (fallback when CUDA not available or for small datasets)
    struct ScoredRow {
        int64_t idx;
        float score;
    };
    std::vector<ScoredRow> candidates;
    candidates.reserve(std::min(g_handle->num_rows, int64_t(100000)));

    for (int64_t i = 0; i < g_handle->num_rows; ++i) {
        float score = 0.0f;

        if (borough_code > 0 && g_handle->boroughs[i] == borough_code)
            score += 2.0f;
        if (complaint_code > 0 && g_handle->complaint_types[i] == complaint_code)
            score += 3.0f;

        if (lat != 0.0f && lon != 0.0f && g_handle->lats[i] != 0.0f) {
            float dlat = g_handle->lats[i] - lat;
            float dlon = g_handle->lons[i] - lon;
            float dist = dlat * dlat + dlon * dlon;
            if (dist < 0.001f) score += 1.0f;  // ~100m radius
            else if (dist < 0.01f) score += 0.5f;
        }

        if (score > 0.0f) {
            candidates.push_back({i, score});
        }
    }

    // Partial sort for top-k
    if (static_cast<int>(candidates.size()) > k) {
        std::partial_sort(
            candidates.begin(),
            candidates.begin() + k,
            candidates.end(),
            [](const ScoredRow& a, const ScoredRow& b) { return a.score > b.score; }
        );
        candidates.resize(k);
    } else {
        std::sort(
            candidates.begin(), candidates.end(),
            [](const ScoredRow& a, const ScoredRow& b) { return a.score > b.score; }
        );
    }

    for (const auto& c : candidates) {
        int64_t i = c.idx;
        py::dict row;
        row["complaint_type"] = CSVParser::complaint_type_str(g_handle->complaint_types[i]);
        row["borough"] = CSVParser::borough_str(g_handle->boroughs[i]);
        row["created_date"] = g_handle->dates[i];
        row["resolution_days"] = g_handle->resolutions[i];
        row["lat"] = g_handle->lats[i];
        row["lon"] = g_handle->lons[i];
        row["score"] = c.score;
        results.push_back(row);
    }

    return results;
}

py::dict benchmark(int n_queries) {
    if (!g_handle || !g_handle->batch) {
        return py::dict(py::arg("error") = "No data loaded");
    }

    std::vector<double> latencies;
    latencies.reserve(n_queries);

    // Test queries across different boroughs and types
    int test_boroughs[] = {1, 2, 3, 4, 5};
    int test_types[] = {1, 2, 3, 17, 18};

    for (int q = 0; q < n_queries; ++q) {
        int borough = test_boroughs[q % 5];
        int type = test_types[q % 5];

        auto start = std::chrono::high_resolution_clock::now();
        search(borough, type, 40.7128f, -74.0060f, 5);
        auto end = std::chrono::high_resolution_clock::now();

        double ms = std::chrono::duration<double, std::milli>(end - start).count();
        latencies.push_back(ms);
    }

    std::sort(latencies.begin(), latencies.end());

    auto percentile = [&](double p) -> double {
        int idx = static_cast<int>(p * latencies.size());
        idx = std::min(idx, static_cast<int>(latencies.size()) - 1);
        return latencies[idx];
    };

    return py::dict(
        py::arg("n_queries") = n_queries,
        py::arg("rows") = g_handle->num_rows,
        py::arg("p50_ms") = percentile(0.50),
        py::arg("p95_ms") = percentile(0.95),
        py::arg("p99_ms") = percentile(0.99),
        py::arg("min_ms") = latencies.front(),
        py::arg("max_ms") = latencies.back(),
        py::arg("mean_ms") = std::accumulate(latencies.begin(), latencies.end(), 0.0) / latencies.size()
    );
}

PYBIND11_MODULE(dispatch_engine, m) {
    m.doc() = "NYC First Responder Dispatch — C++ Engine with CUDA acceleration";

    m.def("load_csv", &load_csv,
          "Load a CSV file into the engine",
          py::arg("path"),
          py::arg("num_threads") = 20);

    m.def("load_ipc", &load_ipc,
          "Load an Arrow IPC file into the engine",
          py::arg("path"));

    m.def("search", &search,
          "Search for matching incidents",
          py::arg("borough_code") = 0,
          py::arg("complaint_code") = 0,
          py::arg("lat") = 0.0f,
          py::arg("lon") = 0.0f,
          py::arg("k") = 5);

    m.def("benchmark", &benchmark,
          "Run benchmark queries",
          py::arg("n_queries") = 100);
}

} // namespace dispatch
