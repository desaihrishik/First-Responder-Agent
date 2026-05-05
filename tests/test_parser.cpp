#include <iostream>
#include <string>
#include "../src/cpp/ingestion/csv_parser.h"

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cerr << "Usage: " << argv[0] << " <csv_file> [num_threads]" << std::endl;
        return 1;
    }

    std::string filepath = argv[1];
    int threads = (argc > 2) ? std::atoi(argv[2]) : 20;

    std::cout << "Parsing: " << filepath << " with " << threads << " threads" << std::endl;

    dispatch::CSVParser parser(threads);
    auto batch = parser.parse(filepath);

    if (!batch) {
        std::cerr << "Parse failed!" << std::endl;
        return 1;
    }

    std::cout << "Schema: " << batch->schema()->ToString() << std::endl;
    std::cout << "Rows: " << batch->num_rows() << std::endl;
    std::cout << "Columns: " << batch->num_columns() << std::endl;

    // Print first 5 rows
    std::cout << "\nFirst 5 rows:" << std::endl;
    auto ct_arr = std::static_pointer_cast<arrow::Int8Array>(batch->column(0));
    auto br_arr = std::static_pointer_cast<arrow::Int8Array>(batch->column(1));
    auto res_arr = std::static_pointer_cast<arrow::FloatArray>(batch->column(3));

    for (int64_t i = 0; i < std::min(int64_t(5), batch->num_rows()); ++i) {
        std::cout << "  " << dispatch::CSVParser::complaint_type_str(ct_arr->Value(i))
                  << " | " << dispatch::CSVParser::borough_str(br_arr->Value(i))
                  << " | resolution: " << res_arr->Value(i) << " days"
                  << std::endl;
    }

    // Write to IPC
    std::string ipc_path = "data/incidents.arrow";
    auto status = parser.write_ipc(batch, ipc_path);
    if (status.ok()) {
        std::cout << "\nArrow IPC written to: " << ipc_path << std::endl;
    } else {
        std::cerr << "IPC write failed: " << status.ToString() << std::endl;
    }

    return 0;
}
