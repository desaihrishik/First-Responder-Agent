#pragma once

#include <string>
#include <vector>
#include <memory>
#include <arrow/api.h>
#include <arrow/ipc/api.h>

namespace dispatch {

struct ParsedRecord {
    int8_t complaint_type;   // enum-encoded
    int8_t borough;          // enum-encoded
    int64_t created_date;    // unix timestamp
    float resolution_days;
    float lat;
    float lon;
};

// Borough enum mapping
enum Borough : int8_t {
    UNKNOWN = 0,
    MANHATTAN = 1,
    BROOKLYN = 2,
    QUEENS = 3,
    BRONX = 4,
    STATEN_ISLAND = 5,
};

// Top complaint type enum (extensible)
enum ComplaintType : int8_t {
    CT_UNKNOWN = 0,
    CT_NOISE = 1,
    CT_HEAT_HOT_WATER = 2,
    CT_STREET_CONDITION = 3,
    CT_PLUMBING = 4,
    CT_WATER_SYSTEM = 5,
    CT_PAINT_PLASTER = 6,
    CT_BLOCKED_DRIVEWAY = 7,
    CT_GENERAL_CONSTRUCTION = 8,
    CT_ILLEGAL_PARKING = 9,
    CT_UNSANITARY_CONDITION = 10,
    CT_RODENT = 11,
    CT_NOISE_COMMERCIAL = 12,
    CT_ELEVATOR = 13,
    CT_ELECTRIC = 14,
    CT_DOOR_WINDOW = 15,
    CT_APPLIANCE = 16,
    CT_FIRE = 17,
    CT_MEDICAL = 18,
    CT_ASSAULT = 19,
    CT_OTHER = 20,
};

class CSVParser {
public:
    explicit CSVParser(int num_threads = 20);
    ~CSVParser();

    // Parse a CSV file into an Arrow RecordBatch
    std::shared_ptr<arrow::RecordBatch> parse(const std::string& filepath);

    // Write RecordBatch to Arrow IPC file
    arrow::Status write_ipc(
        const std::shared_ptr<arrow::RecordBatch>& batch,
        const std::string& output_path
    );

    // Read RecordBatch from Arrow IPC file
    std::shared_ptr<arrow::RecordBatch> read_ipc(const std::string& filepath);

    // Get row count from last parse
    int64_t row_count() const { return row_count_; }

    // Get complaint type string from enum
    static std::string complaint_type_str(int8_t code);

    // Get borough string from enum
    static std::string borough_str(int8_t code);

private:
    int num_threads_;
    int64_t row_count_ = 0;

    // Phase 1: find newline offsets in parallel
    std::vector<int64_t> find_offsets(const char* data, int64_t size);

    // Phase 2: parse rows in parallel into column builders
    arrow::Status parse_rows(
        const char* data,
        const std::vector<int64_t>& offsets,
        arrow::Int8Builder& complaint_type_builder,
        arrow::Int8Builder& borough_builder,
        arrow::Int64Builder& date_builder,
        arrow::FloatBuilder& resolution_builder,
        arrow::FloatBuilder& lat_builder,
        arrow::FloatBuilder& lon_builder
    );

    // Map strings to enums
    static int8_t map_borough(const std::string& s);
    static int8_t map_complaint_type(const std::string& s);
    static int64_t parse_date(const std::string& s);
    static float parse_float(const std::string& s);

    // CSV field extraction
    static std::vector<std::string> split_csv_line(const char* line, int64_t len);
};

} // namespace dispatch
