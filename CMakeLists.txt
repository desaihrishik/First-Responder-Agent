cmake_minimum_required(VERSION 3.24)
project(dispatch_engine LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CUDA_STANDARD 17)
set(CMAKE_CUDA_STANDARD_REQUIRED ON)

# Blackwell GPU architecture (sm_90)
set(CMAKE_CUDA_ARCHITECTURES 90)

# ARM64 optimization flags
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -march=armv8-a")
set(CMAKE_CUDA_FLAGS "${CMAKE_CUDA_FLAGS} -O3")

# Find dependencies
find_package(CUDAToolkit REQUIRED)
find_package(Arrow REQUIRED)
find_package(pybind11 REQUIRED)

# Python module target
pybind11_add_module(dispatch_engine
    src/cpp/api/bindings.cpp
    src/cpp/ingestion/csv_parser.cpp
    src/cpp/kernels/filter.cu
)

target_include_directories(dispatch_engine PRIVATE
    ${CMAKE_SOURCE_DIR}/src/cpp
    ${CUDAToolkit_INCLUDE_DIRS}
)

target_link_libraries(dispatch_engine PRIVATE
    Arrow::arrow_shared
    CUDA::cudart
)

# Set output to project root so Python can import it
set_target_properties(dispatch_engine PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}
    CUDA_SEPARABLE_COMPILATION ON
)

# Optional: standalone CLI for testing CSV parser
add_executable(csv_parse_test
    src/cpp/ingestion/csv_parser.cpp
    src/cpp/kernels/filter.cu
)

target_sources(csv_parse_test PRIVATE
    ${CMAKE_SOURCE_DIR}/tests/test_parser.cpp
)

target_include_directories(csv_parse_test PRIVATE
    ${CMAKE_SOURCE_DIR}/src/cpp
    ${CUDAToolkit_INCLUDE_DIRS}
)

target_link_libraries(csv_parse_test PRIVATE
    Arrow::arrow_shared
    CUDA::cudart
)

set_target_properties(csv_parse_test PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/build
    CUDA_SEPARABLE_COMPILATION ON
)

# Print configuration
message(STATUS "CUDA architectures: ${CMAKE_CUDA_ARCHITECTURES}")
message(STATUS "Arrow found: ${Arrow_FOUND}")
message(STATUS "pybind11 found: ${pybind11_FOUND}")
message(STATUS "CXX flags: ${CMAKE_CXX_FLAGS}")
message(STATUS "CUDA flags: ${CMAKE_CUDA_FLAGS}")
