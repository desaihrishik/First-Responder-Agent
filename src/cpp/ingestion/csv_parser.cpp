#include "csv_parser.h"

#include <fstream>
#include <thread>
#include <mutex>
#include <algorithm>
#include <cstring>
#include <ctime>
#include <chrono>
#include <iostream>
#include <sstream>
#include <unordered_map>
#include <cctype>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

#include <arrow/builder.h>
#include <arrow/record_batch.h>
#include <arrow/ipc/writer.h>
#include <arrow/ipc/reader.h>
#include <arrow/io/file.h>

namespace dispatch {

static std::string to_upper(const std::string& s) {
    std::string result = s;
    for (auto& c : result) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return result;
}

static std::string trim(const std::string& s) {
    auto start = s.find_first_not_of(" \t\r\n\"");
    if (start == std::string::npos) return "";
    auto end = s.find_last_not_of(" \t\r\n\"");
    return s.substr(start, end - start + 1);
}

CSVParser::CSVParser(int num_threads) : num_threads_(num_threads) {}

CSVParser::~CSVParser() = default;

int8_t CSVParser::map_borough(const std::string& s) {
    std::string upper = to_upper(trim(s));
    if (upper == "MANHATTAN" || upper == "NEW YORK") return Borough::MANHATTAN;
    if (upper == "BROOKLYN" || upper == "KINGS") return Borough::BROOKLYN;
    if (upper == "QUEENS") return Borough::QUEENS;
    if (upper == "BRONX") return Borough::BRONX;
    if (upper == "STATEN ISLAND" || upper == "RICHMOND") return Borough::STATEN_ISLAND;
    return Borough::UNKNOWN;
}

int8_t CSVParser::map_complaint_type(const std::string& s) {
    std::string upper = to_upper(trim(s));
    static const std::unordered_map<std::string, int8_t> type_map = {
        {"NOISE - RESIDENTIAL", CT_NOISE},
        {"NOISE", CT_NOISE},
        {"NOISE - STREET/SIDEWALK", CT_NOISE},
        {"NOISE - VEHICLE", CT_NOISE},
        {"NOISE - COMMERCIAL", CT_NOISE_COMMERCIAL},
        {"NOISE - PARK", CT_NOISE},
        {"HEAT/HOT WATER", CT_HEAT_HOT_WATER},
        {"HEATING", CT_HEAT_HOT_WATER},
        {"STREET CONDITION", CT_STREET_CONDITION},
        {"PLUMBING", CT_PLUMBING},
        {"WATER SYSTEM", CT_WATER_SYSTEM},
        {"PAINT/PLASTER", CT_PAINT_PLASTER},
        {"BLOCKED DRIVEWAY", CT_BLOCKED_DRIVEWAY},
        {"GENERAL CONSTRUCTION", CT_GENERAL_CONSTRUCTION},
        {"ILLEGAL PARKING", CT_ILLEGAL_PARKING},
        {"UNSANITARY CONDITION", CT_UNSANITARY_CONDITION},
        {"RODENT", CT_RODENT},
        {"ELEVATOR", CT_ELEVATOR},
        {"ELECTRIC", CT_ELECTRIC},
        {"DOOR/WINDOW", CT_DOOR_WINDOW},
        {"APPLIANCE", CT_APPLIANCE},
    };

    auto it = type_map.find(upper);
    if (it != type_map.end()) return it->second;

    if (upper.find("FIRE") != std::string::npos) return CT_FIRE;
    if (upper.find("MEDICAL") != std::string::npos || upper.find("EMS") != std::string::npos) return CT_MEDICAL;
    if (upper.find("ASSAULT") != std::string::npos) return CT_ASSAULT;
    if (upper.find("NOISE") != std::string::npos) return CT_NOISE;

    return CT_OTHER;
}

int64_t CSVParser::parse_date(const std::string& s) {
    std::string trimmed = trim(s);
    if (trimmed.empty()) return 0;

    struct tm tm_val = {};
    // Try MM/DD/YYYY HH:MM:SS AM/PM format
    if (strptime(trimmed.c_str(), "%m/%d/%Y %I:%M:%S %p", &tm_val) ||
        strptime(trimmed.c_str(), "%m/%d/%Y %H:%M:%S", &tm_val) ||
        strptime(trimmed.c_str(), "%Y-%m-%dT%H:%M:%S", &tm_val) ||
        strptime(trimmed.c_str(), "%m/%d/%Y", &tm_val)) {
        return static_cast<int64_t>(timegm(&tm_val));
    }
    return 0;
}

float CSVParser::parse_float(const std::string& s) {
    std::string trimmed = trim(s);
    if (trimmed.empty()) return 0.0f;
    try {
        return std::stof(trimmed);
    } catch (...) {
        return 0.0f;
    }
}

std::string CSVParser::complaint_type_str(int8_t code) {
    static const char* names[] = {
        "Unknown", "Noise", "Heat/Hot Water", "Street Condition",
        "Plumbing", "Water System", "Paint/Plaster", "Blocked Driveway",
        "General Construction", "Illegal Parking", "Unsanitary Condition",
        "Rodent", "Noise - Commercial", "Elevator", "Electric",
        "Door/Window", "Appliance", "Fire", "Medical", "Assault", "Other"
    };
    if (code >= 0 && code <= 20) return names[code];
    return "Unknown";
}

std::string CSVParser::borough_str(int8_t code) {
    static const char* names[] = {
        "Unknown", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"
    };
    if (code >= 0 && code <= 5) return names[code];
    return "Unknown";
}

std::vector<std::string> CSVParser::split_csv_line(const char* line, int64_t len) {
    std::vector<std::string> fields;
    std::string current;
    bool in_quotes = false;

    for (int64_t i = 0; i < len; ++i) {
        char c = line[i];
        if (c == '"') {
            in_quotes = !in_quotes;
        } else if (c == ',' && !in_quotes) {
            fields.push_back(current);
            current.clear();
        } else if (c != '\r' && c != '\n') {
            current += c;
        }
    }
    fields.push_back(current);
    return fields;
}

std::vector<int64_t> CSVParser::find_offsets(const char* data, int64_t size) {
    std::vector<int64_t> offsets;
    offsets.push_back(0);

    std::vector<std::vector<int64_t>> thread_offsets(num_threads_);
    int64_t chunk_size = size / num_threads_;

    std::vector<std::thread> threads;
    for (int t = 0; t < num_threads_; ++t) {
        int64_t start = t * chunk_size;
        int64_t end = (t == num_threads_ - 1) ? size : (t + 1) * chunk_size;

        threads.emplace_back([&, t, start, end]() {
            for (int64_t i = start; i < end; ++i) {
                if (data[i] == '\n' && i + 1 < size) {
                    thread_offsets[t].push_back(i + 1);
                }
            }
        });
    }

    for (auto& th : threads) th.join();

    for (auto& to : thread_offsets) {
        offsets.insert(offsets.end(), to.begin(), to.end());
    }

    std::sort(offsets.begin(), offsets.end());
    offsets.erase(std::unique(offsets.begin(), offsets.end()), offsets.end());

    return offsets;
}

arrow::Status CSVParser::parse_rows(
    const char* data,
    const std::vector<int64_t>& offsets,
    arrow::Int8Builder& complaint_type_builder,
    arrow::Int8Builder& borough_builder,
    arrow::Int64Builder& date_builder,
    arrow::FloatBuilder& resolution_builder,
    arrow::FloatBuilder& lat_builder,
    arrow::FloatBuilder& lon_builder
) {
    if (offsets.size() < 2) return arrow::Status::OK();

    // Parse header to find column indices
    int64_t header_end = offsets[1];
    auto header_fields = split_csv_line(data, header_end);

    int col_complaint = -1, col_borough = -1, col_created = -1, col_closed = -1;
    int col_lat = -1, col_lon = -1;

    for (int i = 0; i < static_cast<int>(header_fields.size()); ++i) {
        std::string field = to_upper(trim(header_fields[i]));
        if (field == "COMPLAINT TYPE" || field == "COMPLAINT_TYPE") col_complaint = i;
        else if (field == "BOROUGH") col_borough = i;
        else if (field == "CREATED DATE" || field == "CREATED_DATE") col_created = i;
        else if (field == "CLOSED DATE" || field == "CLOSED_DATE") col_closed = i;
        else if (field == "LATITUDE") col_lat = i;
        else if (field == "LONGITUDE") col_lon = i;
    }

    int64_t num_rows = static_cast<int64_t>(offsets.size()) - 1;
    std::mutex builder_mutex;

    auto parse_chunk = [&](int64_t row_start, int64_t row_end) {
        std::vector<int8_t> local_complaint;
        std::vector<int8_t> local_borough;
        std::vector<int64_t> local_date;
        std::vector<float> local_resolution;
        std::vector<float> local_lat;
        std::vector<float> local_lon;

        local_complaint.reserve(row_end - row_start);
        local_borough.reserve(row_end - row_start);
        local_date.reserve(row_end - row_start);
        local_resolution.reserve(row_end - row_start);
        local_lat.reserve(row_end - row_start);
        local_lon.reserve(row_end - row_start);

        for (int64_t r = row_start; r < row_end; ++r) {
            int64_t line_start = offsets[r];
            int64_t line_end = (r + 1 < static_cast<int64_t>(offsets.size())) ? offsets[r + 1] : line_start;
            int64_t line_len = line_end - line_start;
            if (line_len <= 1) continue;

            auto fields = split_csv_line(data + line_start, line_len);

            int8_t ct = CT_OTHER;
            if (col_complaint >= 0 && col_complaint < static_cast<int>(fields.size()))
                ct = map_complaint_type(fields[col_complaint]);
            local_complaint.push_back(ct);

            int8_t br = Borough::UNKNOWN;
            if (col_borough >= 0 && col_borough < static_cast<int>(fields.size()))
                br = map_borough(fields[col_borough]);
            local_borough.push_back(br);

            int64_t created = 0;
            if (col_created >= 0 && col_created < static_cast<int>(fields.size()))
                created = parse_date(fields[col_created]);
            local_date.push_back(created);

            float resolution = -1.0f;
            if (col_created >= 0 && col_closed >= 0 &&
                col_created < static_cast<int>(fields.size()) &&
                col_closed < static_cast<int>(fields.size())) {
                int64_t closed = parse_date(fields[col_closed]);
                if (created > 0 && closed > created) {
                    resolution = static_cast<float>(closed - created) / 86400.0f;
                }
            }
            local_resolution.push_back(resolution);

            float lat = 0.0f;
            if (col_lat >= 0 && col_lat < static_cast<int>(fields.size()))
                lat = parse_float(fields[col_lat]);
            local_lat.push_back(lat);

            float lon = 0.0f;
            if (col_lon >= 0 && col_lon < static_cast<int>(fields.size()))
                lon = parse_float(fields[col_lon]);
            local_lon.push_back(lon);
        }

        std::lock_guard<std::mutex> lock(builder_mutex);
        for (auto v : local_complaint) complaint_type_builder.UnsafeAppend(v);
        for (auto v : local_borough) borough_builder.UnsafeAppend(v);
        for (auto v : local_date) date_builder.UnsafeAppend(v);
        for (auto v : local_resolution) resolution_builder.UnsafeAppend(v);
        for (auto v : local_lat) lat_builder.UnsafeAppend(v);
        for (auto v : local_lon) lon_builder.UnsafeAppend(v);
    };

    // Skip header row (index 0), start from row 1
    int64_t data_rows = num_rows;
    int64_t rows_per_thread = std::max(int64_t(1), data_rows / num_threads_);

    // Pre-allocate builders
    ARROW_RETURN_NOT_OK(complaint_type_builder.Reserve(data_rows));
    ARROW_RETURN_NOT_OK(borough_builder.Reserve(data_rows));
    ARROW_RETURN_NOT_OK(date_builder.Reserve(data_rows));
    ARROW_RETURN_NOT_OK(resolution_builder.Reserve(data_rows));
    ARROW_RETURN_NOT_OK(lat_builder.Reserve(data_rows));
    ARROW_RETURN_NOT_OK(lon_builder.Reserve(data_rows));

    std::vector<std::thread> threads;
    for (int t = 0; t < num_threads_; ++t) {
        int64_t start = 1 + t * rows_per_thread;  // skip header
        int64_t end = (t == num_threads_ - 1) ? (num_rows + 1) : (1 + (t + 1) * rows_per_thread);
        end = std::min(end, static_cast<int64_t>(offsets.size()));
        if (start >= end) continue;
        threads.emplace_back(parse_chunk, start, end);
    }
    for (auto& th : threads) th.join();

    return arrow::Status::OK();
}

std::shared_ptr<arrow::RecordBatch> CSVParser::parse(const std::string& filepath) {
    auto start_time = std::chrono::high_resolution_clock::now();

    int fd = open(filepath.c_str(), O_RDONLY);
    if (fd < 0) {
        std::cerr << "Failed to open: " << filepath << std::endl;
        return nullptr;
    }

    struct stat st;
    fstat(fd, &st);
    int64_t file_size = st.st_size;

    const char* data = static_cast<const char*>(
        mmap(nullptr, file_size, PROT_READ, MAP_PRIVATE, fd, 0)
    );
    if (data == MAP_FAILED) {
        close(fd);
        std::cerr << "mmap failed for: " << filepath << std::endl;
        return nullptr;
    }

    // Advise sequential access
    madvise(const_cast<char*>(data), file_size, MADV_SEQUENTIAL);

    std::cout << "Phase 1: Finding line offsets (" << num_threads_ << " threads)..." << std::endl;
    auto offsets = find_offsets(data, file_size);
    std::cout << "  Found " << offsets.size() << " lines" << std::endl;

    std::cout << "Phase 2: Parsing rows into Arrow columns..." << std::endl;
    arrow::Int8Builder complaint_type_builder;
    arrow::Int8Builder borough_builder;
    arrow::Int64Builder date_builder;
    arrow::FloatBuilder resolution_builder;
    arrow::FloatBuilder lat_builder;
    arrow::FloatBuilder lon_builder;

    auto status = parse_rows(
        data, offsets,
        complaint_type_builder, borough_builder, date_builder,
        resolution_builder, lat_builder, lon_builder
    );

    munmap(const_cast<char*>(data), file_size);
    close(fd);

    if (!status.ok()) {
        std::cerr << "Parse failed: " << status.ToString() << std::endl;
        return nullptr;
    }

    std::shared_ptr<arrow::Array> complaint_arr, borough_arr, date_arr;
    std::shared_ptr<arrow::Array> resolution_arr, lat_arr, lon_arr;

    complaint_type_builder.Finish(&complaint_arr);
    borough_builder.Finish(&borough_arr);
    date_builder.Finish(&date_arr);
    resolution_builder.Finish(&resolution_arr);
    lat_builder.Finish(&lat_arr);
    lon_builder.Finish(&lon_arr);

    auto schema = arrow::schema({
        arrow::field("complaint_type", arrow::int8()),
        arrow::field("borough", arrow::int8()),
        arrow::field("created_date", arrow::int64()),
        arrow::field("resolution_days", arrow::float32()),
        arrow::field("lat", arrow::float32()),
        arrow::field("lon", arrow::float32()),
    });

    auto batch = arrow::RecordBatch::Make(
        schema, complaint_arr->length(),
        {complaint_arr, borough_arr, date_arr, resolution_arr, lat_arr, lon_arr}
    );

    row_count_ = batch->num_rows();

    auto end_time = std::chrono::high_resolution_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(end_time - start_time);
    std::cout << "Parsed " << row_count_ << " rows in " << elapsed.count() << "ms" << std::endl;

    return batch;
}

arrow::Status CSVParser::write_ipc(
    const std::shared_ptr<arrow::RecordBatch>& batch,
    const std::string& output_path
) {
    auto result = arrow::io::FileOutputStream::Open(output_path);
    if (!result.ok()) return result.status();
    auto outfile = *result;

    auto writer_result = arrow::ipc::MakeFileWriter(outfile, batch->schema());
    if (!writer_result.ok()) return writer_result.status();
    auto writer = *writer_result;

    ARROW_RETURN_NOT_OK(writer->WriteRecordBatch(*batch));
    ARROW_RETURN_NOT_OK(writer->Close());
    ARROW_RETURN_NOT_OK(outfile->Close());

    return arrow::Status::OK();
}

std::shared_ptr<arrow::RecordBatch> CSVParser::read_ipc(const std::string& filepath) {
    auto result = arrow::io::ReadableFile::Open(filepath);
    if (!result.ok()) return nullptr;
    auto infile = *result;

    auto reader_result = arrow::ipc::RecordBatchFileReader::Open(infile);
    if (!reader_result.ok()) return nullptr;
    auto reader = *reader_result;

    auto batch_result = reader->ReadRecordBatch(0);
    if (!batch_result.ok()) return nullptr;

    return *batch_result;
}

} // namespace dispatch
