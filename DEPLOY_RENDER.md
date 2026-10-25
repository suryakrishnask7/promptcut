# Deploying PromptCut on Render (render.com)

This guide walks you through deploying the **PromptCut** Prompt-Based Video Editing Engine to [Render](https://render.com).

---

## Why Docker on Render?
PromptCut relies on **FFmpeg** for video trimming, audio normalization, filtergraphs, and ASS subtitle rendering. Deploying with **Docker** is the recommended method because the included [Dockerfile](file:///d:/Project#2/Dockerfile) packages:
- Python 3.11
- System FFmpeg & ffprobe
- System font packages (`fonts-dejavu-core`) for ASS subtitle burn-in
- Dynamic `$PORT` binding for Render cloud routing

---

## Method 1: 1-Click Blueprint (Recommended)

PromptCut includes a pre-configured [render.yaml](file:///d:/Project#2/render.yaml) Blueprint specification.

1. **Push your code** to GitHub or GitLab.
2. Log into the [Render Dashboard](https://dashboard.render.com/).
3. Click **New +** in the top navigation bar and select **Blueprint**.
4. Connect and select your Git repository.
5. Render will automatically detect [render.yaml](file:///d:/Project#2/render.yaml) and configure:
   - **Service Name**: `promptcut`
   - **Runtime**: `Docker`
   - **Health Check Path**: `/health`
   - **Environment Variables**:
     - `TIME_POLICY=original`
     - `STORAGE_BACKEND=local`
     - `SILENCE_SNAP_TOLERANCE_S=0.15`
     - `INCUBRIX_WEBHOOK_SECRET` (auto-generated)
6. Under **Environment Variables**, provide your `GROQ_API_KEY` (for natural language LLM transcription & instruction parsing).
7. Click **Apply**.
8. Render will build the Docker container and deploy the service.

---

## Method 2: Manual Web Service Setup (Docker)

If you prefer setting up the Web Service manually via the Render UI:

1. In the Render Dashboard, click **New +** -> **Web Service**.
2. Select **Build and deploy from a Git repository** and pick your repository.
3. Configure the service settings:
   - **Name**: `promptcut` (or any name you choose)
   - **Region**: `Oregon (US West)` or `Frankfurt (EU Central)`
   - **Branch**: `master` (or `main`)
   - **Runtime**: `Docker`
   - **Dockerfile Path**: `./Dockerfile`
   - **Docker Context**: `.`
   - **Plan**: `Free` or `Starter`
4. Expand **Advanced** and set:
   - **Health Check Path**: `/health`
5. Under **Environment Variables**, add:
   | Key | Recommended Value | Notes |
   |:---|:---|:---|
   | `PORT` | `10000` | Render injects this automatically |
   | `HOST` | `0.0.0.0` | Listens on all interfaces |
   | `GROQ_API_KEY` | `gsk_...` | Your Groq API key for LLM interpretation |
   | `TIME_POLICY` | `original` | `original` or `output` |
   | `STORAGE_BACKEND` | `local` | `local` (or `s3` with AWS/MinIO credentials) |
   | `UVICORN_RELOAD` | `false` | Disable watcher in production |
6. Click **Create Web Service**.

---

## Verifying Your Deployed Service

Once the build finishes and status displays **Live**:

1. **Interactive Web UI**:
   - Open: `https://<your-service-name>.onrender.com/` (automatically redirects to `/ui`)
   - Test by clicking **"Load Sample Session"** or uploading a video.

2. **Health & Readiness Endpoints**:
   - Liveness: `https://<your-service-name>.onrender.com/health` (returns `{"status":"ok"}`)
   - Readiness: `https://<your-service-name>.onrender.com/ready` (verifies storage, ffmpeg, interpreter)

3. **OpenAPI Interactive Documentation**:
   - Swagger Docs: `https://<your-service-name>.onrender.com/docs`
   - ReDoc: `https://<your-service-name>.onrender.com/redoc`

4. **Running Batch Tests Against Your Live Render URL**:
   You can run the batch runner CLI tool against your live Render deployment from your local machine:
   ```bash
   python scripts/batch_runner.py eval_benchmark.yaml --api-url https://<your-service-name>.onrender.com
   ```
