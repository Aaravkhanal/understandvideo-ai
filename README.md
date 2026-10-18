<div align="center">

# 🎬 understandvideo-ai

**Next-Generation Multimodal Video Understanding & Conversational AI**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch 2.0+](https://img.shields.io/badge/PyTorch-2.0+-EE4C2C.svg?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Built by](https://img.shields.io/badge/Author-Aarav%20Khanal-10b981.svg?style=for-the-badge&logo=github&logoColor=white)](https://github.com/aaravkhanal)

<p align="center">
  <a href="#-key-features">Key Features</a> •
  <a href="#-architecture">Architecture</a> •
  <a href="#-quickstart">Quickstart</a> •
  <a href="#-interactive-demo">Interactive Demo</a> •
  <a href="#-benchmarks">Benchmarks</a> •
  <a href="#-training">Training</a> •
  <a href="#-license">License</a>
</p>

---

</div>

## 📌 Overview

**understandvideo-ai** is an end-to-end multimodal conversational AI system built for deep video comprehension. By harmonizing spatiotemporal visual feature representations with large autoregressive language models, **understandvideo-ai** enables detailed video question answering, temporal reasoning, dense video summarization, and multi-turn conversational interactions about complex video scenes.

<p align="center">
  <img src="docs/images/Video-ChatGPT.gif" alt="understandvideo-ai Architecture Demo" width="90%" style="border-radius: 8px;">
</p>

---

## ✨ Key Features

- **Spatiotemporal Visual Encoding**: Combines frame-level spatial feature extraction (CLIP ViT-L/14) with custom temporal pooling to model dynamic actions and continuous scenes.
- **Context-Aware Conversational QA**: Retains conversational history across multiple turns, enabling detailed exploration of visual events, objects, actions, and intent.
- **Zero-Shot Generalization**: Outperforms prior models on open benchmarks including MSVD-QA, MSRVTT-QA, TGIF-QA, and ActivityNet-QA without dataset-specific re-training.
- **Rigorous Evaluation Framework**: Includes built-in evaluation suites measuring correctness, detail orientation, contextual comprehension, temporal precision, and consistency.
- **Turnkey Interactive UI**: Powered by Gradio for quick local deployment and seamless video-upload-and-chat experimentation.
- **Scalable Data & Training Pipeline**: Includes data preprocessing, feature precomputation, instruction tuning, and model weight merging utilities.

---

## 🏛️ Architecture

<p align="center">
  <img src="docs/images/hightlights_video_chatgpt.png" alt="understandvideo-ai System Highlights" width="90%" style="border-radius: 8px;">
</p>

1. **Video Feature Extraction**: Input video frames are sampled and encoded into spatiotemporal tokens using a visual transformer encoder.
2. **Multimodal Projection**: A linear adapter layer bridges visual token dimensions into the language model embedding space.
3. **Autoregressive Decoding**: Visual prefix tokens condition the language model to generate fluent, contextually accurate conversational responses.

---

## 🚀 Quickstart

### 1. Prerequisites & Environment Setup

Ensure you have [Conda](https://docs.conda.io/en/latest/) and an NVIDIA GPU with at least 16GB VRAM (or compatible PyTorch setup).

```bash
# Clone the repository
git clone https://github.com/Aaravkhanal/understandvideo-ai.git
cd understandvideo-ai

# Create and activate environment
conda create -n understandvideo-ai python=3.10 -y
conda activate understandvideo-ai

# Install dependencies
pip install -r requirements.txt
export PYTHONPATH="./:$PYTHONPATH"
```

### 2. Optional: FlashAttention Installation (Recommended for Training)
```bash
pip install ninja
git clone https://github.com/HazyResearch/flash-attention.git
cd flash-attention
git checkout v1.0.7
python setup.py install
cd ..
```

---

## 💻 Interactive Demo

### Modern workspace (recommended)

The repository now includes a polished React workspace and a FastAPI service. The
frontend runs in a self-contained demo mode out of the box, so video upload,
playback, scene navigation, timestamp jumps, chat streaming, and history can be
explored without downloading model weights.

UnderstandVideo is a **video understanding and conversational Q&A platform**. It
does not generate or edit videos. Users provide an existing video, then ask
questions about its events, people, objects, setting, sequence, or meaning. Every
answer should remain grounded in the uploaded video and link back to supporting
timestamps whenever possible.

```bash
# Install the web and API dependencies once
npm install
python -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt

# Start the complete platform
npm run dev
```

The command opens the correct app at `http://localhost:4310` and starts its API
on `http://localhost:8787`. These dedicated ports avoid collisions with other
local projects. API documentation is available at `http://localhost:8787/docs`.

Use the **Try the 12 sec demo** action to explore the complete upload, analysis,
scene map, timestamp navigation, overview, and conversational flow with the
included cinematic sample. If the API is unavailable, the interface gracefully
continues in browser-only demo mode.

For real semantic Q&A over arbitrary uploaded videos, configure a vision model
before starting the app:

```bash
export OPENAI_API_KEY="your-key"
# Optional; defaults to gpt-4.1-mini
export OPENAI_VISION_MODEL="gpt-4.1-mini"
npm run dev
```

The server uniformly samples frames, attaches their timestamps, and sends those
frames with the question and recent conversation context. Without credentials,
the included sample remains fully interactive and other uploads show a clear
configuration message instead of returning invented answers.

The server defaults to `INFERENCE_MODE=demo`. Its summary and SSE chat endpoints
are deliberately isolated behind `server/main.py`, providing a single integration
point for either the local `video_chatgpt` inference code or a hosted multimodal
provider. Uploaded media and the local SQLite database are ignored by Git.

See [Platform Roadmap](docs/PLATFORM_ROADMAP.md) for the prioritized path from
this local foundation to a production SaaS release.

### Legacy Gradio demo

Launch the local interactive Gradio dashboard:

```bash
python video_chatgpt/demo/video_demo.py \
    --model-name <path-to-LLaVA-model-weights> \
    --projection_path <path-to-model-checkpoint>
```

Once running, navigate to `http://localhost:7860` in your web browser. Upload an MP4 video clip, type questions in natural language, and inspect generated video descriptions in real-time.

For detailed offline preparation, see [Offline Demo Guide](docs/offline_demo.md).

---

## 📊 Benchmarks

### 1. Zero-Shot Video Question Answering

| Model | MSVD-QA Acc | MSVD-QA Score | MSRVTT-QA Acc | MSRVTT-QA Score | TGIF-QA Acc | TGIF-QA Score | ActivityNet-QA Acc | ActivityNet-QA Score |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| FrozenBiLM | 32.2 | -- | 16.8 | -- | 41.0 | -- | 24.7 | -- |
| Video Chat | 56.3 | 2.8 | 45.0 | 2.5 | 34.4 | 2.3 | 26.5 | 2.2 |
| LLaMA Adapter | 54.9 | 3.1 | 43.8 | 2.7 | - | - | 34.2 | 2.7 |
| Video LLaMA | 51.6 | 2.5 | 29.6 | 1.8 | - | - | 12.4 | 1.1 |
| **understandvideo-ai** | **64.9** | **3.3** | **49.3** | **2.8** | **51.4** | **3.0** | **35.2** | **2.7** |

---

### 2. Generative Performance Benchmarking

| Metric Aspect | Video Chat | LLaMA Adapter | Video LLaMA | **understandvideo-ai** |
| :--- | :---: | :---: | :---: | :---: |
| **Correctness of Information** | 2.23 | 2.03 | 1.96 | **2.40** |
| **Detail Orientation** | 2.50 | 2.32 | 2.18 | **2.52** |
| **Contextual Understanding** | 2.53 | 2.30 | 2.16 | **2.62** |
| **Temporal Understanding** | 1.94 | **1.98** | 1.82 | **1.98** |
| **Consistency** | 2.24 | 2.15 | 1.79 | **2.37** |

---

## 🖼️ Qualitative Capabilities

<details open>
<summary><b>Click to expand qualitative demonstration samples</b></summary>
<br>

### Video Reasoning & Action Recognition
![sample1](docs/demo_samples/video_reasoning-min.png)
![sample22](docs/demo_samples/action_recognition-min.png)

### Creative Synthesis & Question Answering
![sample5](docs/demo_samples/creative_and_generative-min.png)
![sample14](docs/demo_samples/question_answering-min.png)

### Temporal & Spatial Scene Comprehension
![sample18](docs/demo_samples/temporal_understanding-min.png)
![sample8](docs/demo_samples/spatial_understanding-min.png)

</details>

---

## 🛠️ Training & Fine-Tuning

To train or fine-tune **understandvideo-ai** across 8 GPUs using PyTorch Distributed / Torchrun:

```bash
torchrun --nproc_per_node=8 --master_port 29001 video_chatgpt/train/train_mem.py \
    --model_name_or_path <path-to-base-model> \
    --version v1 \
    --data_path <path-to-training-instruction-json> \
    --video_folder <path-to-spatio-temporal-features> \
    --tune_mm_mlp_adapter True \
    --mm_use_vid_start_end \
    --bf16 True \
    --output_dir ./checkpoints \
    --num_train_epochs 3 \
    --per_device_train_batch_size 4 \
    --per_device_eval_batch_size 4 \
    --gradient_accumulation_steps 1 \
    --evaluation_strategy "no" \
    --save_strategy "steps" \
    --save_steps 3000 \
    --learning_rate 2e-5 \
    --warmup_ratio 0.03 \
    --lr_scheduler_type "cosine" \
    --logging_steps 100 \
    --model_max_length 2048 \
    --gradient_checkpointing True \
    --lazy_preprocess True
```

For data formatting and custom video annotation generation, refer to:
- [Training Guide](docs/train_video_chatgpt.md)
- [Dataset Guide](data/README.md)

---

## 📈 Evaluation Suites

Run quantitative zero-shot evaluations:

```bash
# General multi-criteria benchmark
bash quantitative_evaluation/evaluate_benchmark.sh

# ActivityNet-QA benchmark
python quantitative_evaluation/evaluate_activitynet_qa.py \
    --pred_path <path-to-predictions.json> \
    --output_dir ./eval_results \
    --output_json results.json \
    --api_key <your-openai-api-key>
```

For more documentation, see [Quantitative Evaluation Guide](quantitative_evaluation/README.md).

---

## 📂 Project Structure

```text
understandvideo-ai/
├── data/                         # Instruction data generation & tooling
├── docs/                         # Extended documentation & sample visual assets
├── quantitative_evaluation/      # Benchmarking scripts and evaluation tools
├── scripts/                      # Delta weight application and feature extraction
├── video_chatgpt/                # Core modeling, conversation engine & demo UI
│   ├── demo/                     # Gradio interface & web server components
│   ├── eval/                     # Model inference runners
│   ├── model/                    # PyTorch model definitions & projections
│   └── train/                    # Distributed fine-tuning scripts
├── requirements.txt              # Environment dependencies
├── LICENSE                       # MIT License
└── README.md                     # Project documentation
```

---

## 👤 Author

**Aarav Khanal**
- GitHub: [@aaravkhanal](https://github.com/aaravkhanal)
- Issues & Suggestions: [Open an Issue](https://github.com/Aaravkhanal/understandvideo-ai/issues)

---

## 📜 License

This project is open-source and released under the [MIT License](LICENSE).
