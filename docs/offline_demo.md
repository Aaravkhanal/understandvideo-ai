# Running Video-ChatGPT Demo Offline
Please follow the instructions below to run the Video-ChatGPT demo on your local GPU machine. 

Note: Our demo requires approximately 18 GB of GPU memory.

### Clone the repository

```shell
git clone https://github.com/Aaravkhanal/understandvideo-ai.git
cd understandvideo-ai
export PYTHONPATH="./:$PYTHONPATH"
```

### Download Model weights

```shell
wget https://huggingface.co/MBZUAI/Video-ChatGPT-7B/resolve/main/video_chatgpt-7B.bin
```
More details at: [https://huggingface.co/MBZUAI/Video-ChatGPT-7B](https://huggingface.co/MBZUAI/Video-ChatGPT-7B).

### Prepare LLaVA weights

UnderstandVideo AI is built using LLaVA. Please follow the instructions below to get LLaVA weights.

- Get the original LLaMA weights in the huggingface format by following the instructions [here](https://huggingface.co/docs/transformers/main/model_doc/llama).
- Use the following scripts to get LLaVA weights by applying our delta.
```shell
python scripts/apply_delta.py \ 
        --base-model-path <path to LLaMA 7B weights> \
        --target-model-path LLaVA-Lightning-7B-v1-1 \
        --delta-path liuhaotian/LLaVA-Lightning-7B-delta-v1-1
```

The above command will download the LLaVA-Lightning-7B-v1-1 delta from HuggingFace, apply it to the provided LLaMA 
weights and save the LLaVA-Lightning-7B-v1-1 weights in the current directory.

Alternatively you can download ready LLaVA-Lightning-7B weights directly from HuggingFace.

### Download sample videos

Place your sample MP4 videos to test the demo inside the `video_chatgpt/demo_sample_videos` directory.

### Run the Demo

```shell
python video_chatgpt/demo/video_demo.py 
        --model-name <path to the LLaVA-Lightening-7B-v1-1 weights prepared in step 3> \
        --projection_path <path to the downloaded video-chatgpt weights>
```
Follow the instructions on the screen to open the demo dashboard.