# Architecture — NYC First Responder Dispatch Intelligence

## Unified Memory: The Core Design Decision

The GB10 Grace Blackwell Superchip provides 128GB of unified LPDDR5x memory shared between the 20-core ARM Grace CPU and the Blackwell GPU. Our architecture exploits this by loading all NYC incident data (2M+ 311/EMS/Fire/NYPD records) into Apache Arrow columnar buffers that reside in a single physical address space accessible to both processors. We call `cudaMemAdvise(SetPreferredLocation, GPU)` followed by `cudaMemPrefetchAsync` at startup, which migrates pages to GPU-local DRAM and eliminates page faults during query execution. The CPU writes Arrow RecordBatches during ingestion; the GPU reads them during filter kernels — no `cudaMemcpy`, no PCIe transfers, no serialization. This is the fundamental advantage of Grace Blackwell over discrete GPU architectures.

## Three-Layer Inference Pipeline

Every dispatch triage flows through three concurrent layers. First, the C++ CUDA engine runs a warp-ballot filter kernel (`__ballot_sync` with 256-thread blocks) across the full 2M-row Arrow buffer, producing a compact bitmask in under 10ms. Simultaneously, ChromaDB performs semantic nearest-neighbor search over our pre-embedded incident corpus using `all-MiniLM-L6-v2` embeddings. If an image is attached, LLaVA 13B generates a scene description via Ollama. These three results — structured filter hits, semantic RAG context, and optional vision description — are assembled into an 800-token context window and fed to Nemotron Nano, which returns a structured JSON triage assessment with category, severity (1–5), agency routing, and a plain-English first responder brief. The entire pipeline completes in under 1 second for text-only queries and under 2 seconds with vision.

## Why This Matters

NYC dispatchers handle 11 million 311 calls per year. Current triage relies on manual lookup tables and cloud-based systems that transmit sensitive incident data to external servers. Our system demonstrates that a single GB10 chip can run the complete AI triage pipeline — vision encoder, vector search, and language model — entirely locally, with no network dependency and no data exfiltration risk. The unified memory architecture means we can scale to the full NYC incident corpus without the memory fragmentation that plagues discrete GPU deployments. Every byte of sensitive dispatch data stays on-device, processed in under a second, on hardware that fits under a desk.
