"""
Phase 1: GLM-OCR SDK + vLLM in ONE Modal GPU container.

The PDF is uploaded once. Inside the container:
  PP-DocLayoutV3 (layout, on GPU) -> crops regions -> GLM-OCR via vLLM on localhost -> JSON + Markdown.
Results come back to your laptop and are saved under --out.

Usage (venv active, in this folder):
  # test on two pages
  modal run modal_glm_ocr_sdk.py --pdf annual-report-2024-2025.pdf --pages 96,252 --vis
  # whole PDF
  modal run modal_glm_ocr_sdk.py --pdf annual-report-2024-2025.pdf

No API key or public URL is needed: `modal run` calls the function through your
Modal login, and vLLM only listens on 127.0.0.1 inside the container.
"""

import time
from pathlib import Path

import modal

MODEL = "zai-org/GLM-OCR"
VLLM_PORT = 8000
MINUTES = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    # Same line as modal_glm_ocr.py -> Modal reuses the already-built layer
    .pip_install("vllm>=0.19.0", "transformers>=5.3.0", "huggingface_hub[hf_transfer]")
    # GLM-OCR SDK. Installed WITHOUT the [selfhosted] extra so pip can't swap the
    # torch/transformers versions vLLM needs; the extra's other deps are listed here.
    .pip_install(
        "glmocr==0.1.5",
        "opencv-python-headless>=4.10.0",
        "pypdfium2>=5.6.0",
        "sentencepiece>=0.2.0",
        "accelerate>=1.13.0",
    ).env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Same volumes as before: GLM-OCR weights are already cached; the layout model
# (PP-DocLayoutV3) is downloaded into the same cache on first run.
hf_cache = modal.Volume.from_name("hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("vllm-cache", create_if_missing=True)

app = modal.App("glm-ocr-sdk")


def build_sdk_config(path: str) -> None:
    """Start from the SDK's own default config and change only what we need."""
    import glmocr
    import yaml

    cfg = yaml.safe_load((Path(glmocr.__file__).parent / "config.yaml").read_text())
    p = cfg["pipeline"]
    p["maas"]["enabled"] = False  # never use Zhipu's cloud
    p["ocr_api"].update(
        api_url=f"http://127.0.0.1:{VLLM_PORT}/v1/chat/completions",  # vLLM in this container
        model="glm-ocr",
        api_key=None,
        verify_ssl=False,
        request_timeout=300,
    )
    p["max_workers"] = 16  # parallel region requests to vLLM
    p["layout"]["device"] = "cuda"  # layout model on the same GPU
    tasks = p["layout"]["label_task_mapping"]
    if "footnote" in tasks["abandon"]:  # keep footnotes (financial notes)
        tasks["abandon"].remove("footnote")
        tasks["text"].append("footnote")
    p["result_formatter"]["output_format"] = "both"  # JSON + Markdown
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False))


@app.cls(
    image=image,
    gpu=["L4", "A10G"],  # take whichever 24 GB GPU is free first
    scaledown_window=2 * MINUTES,  # shut down 2 idle minutes after the last call
    timeout=60 * MINUTES,  # max time for one call (a whole report fits)
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
)
class Parser:
    @modal.enter()
    def start(self):
        """Runs once per container: start vLLM, wait until ready, load the SDK."""
        import subprocess
        import urllib.request

        t0 = time.time()
        self.vllm = subprocess.Popen(
            [
                "vllm",
                "serve",
                MODEL,
                "--served-model-name",
                "glm-ocr",
                "--host",
                "127.0.0.1",
                "--port",
                str(VLLM_PORT),
                "--max-model-len",
                "16384",
                "--gpu-memory-utilization",
                "0.6",  # leave GPU room for the layout model
                "--speculative-config",
                '{"method": "mtp", "num_speculative_tokens": 1}',
            ]
        )
        # Wait for vLLM's health endpoint (first start downloads nothing new: weights are cached)
        while True:
            if self.vllm.poll() is not None:
                raise RuntimeError("vLLM exited during startup; see the logs above")
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{VLLM_PORT}/health", timeout=2
                )
                break
            except Exception:
                time.sleep(2)
            if time.time() - t0 > 15 * MINUTES:
                raise TimeoutError("vLLM not ready after 15 minutes")
        t1 = time.time()

        from glmocr import GlmOcr

        build_sdk_config("/root/glmocr_config.yaml")
        self.parser = GlmOcr(config_path="/root/glmocr_config.yaml", mode="selfhosted")
        self.startup = {
            "vllm_ready_s": round(t1 - t0, 1),
            "sdk_ready_s": round(time.time() - t1, 1),
        }
        print("startup:", self.startup)

    @modal.method()
    def parse_pdf(self, pdf_bytes: bytes, name: str, layout_vis: bool = False) -> dict:
        """Parse one PDF. Returns {"files": {relative_path: bytes}, "timing": {...}}."""
        import shutil
        import tempfile

        work = Path(tempfile.mkdtemp())
        pdf_path = work / f"{name}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        out_dir = work / "out"

        t0 = time.time()
        result = self.parser.parse(str(pdf_path), save_layout_visualization=layout_vis)
        result.save(output_dir=str(out_dir), save_layout_visualization=layout_vis)
        parse_s = round(time.time() - t0, 1)

        files = {
            str(f.relative_to(out_dir)): f.read_bytes()
            for f in out_dir.rglob("*")
            if f.is_file()
        }
        shutil.rmtree(work, ignore_errors=True)
        return {"files": files, "timing": {**self.startup, "parse_s": parse_s}}

    @modal.method()
    def ocr_pages(self, pdf_bytes: bytes, prompts: list, dpi: int = 200) -> list:
        """Whole-page OCR (no layout step): every page x every prompt, 16 at a time.

        Used by reconcile.py to re-read pages whose SDK tables failed the checks.
        Returns [{"index", "prompt", "content", "truncated"}, ...].
        """
        import base64
        import json
        import urllib.request
        from concurrent.futures import ThreadPoolExecutor

        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        images = [base64.b64encode(p.get_pixmap(dpi=dpi).tobytes("png")).decode()
                  for p in doc]

        def one(job):
            index, prompt = job
            body = {
                "model": "glm-ocr",
                "temperature": 0.0,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{images[index]}"}},
                    {"type": "text", "text": prompt},
                ]}],
            }
            req = urllib.request.Request(
                f"http://127.0.0.1:{VLLM_PORT}/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=600) as r:
                choice = json.load(r)["choices"][0]
            return {"index": index, "prompt": prompt,
                    "content": choice["message"]["content"],
                    "truncated": choice.get("finish_reason") == "length"}

        jobs = [(i, p) for i in range(len(images)) for p in prompts]
        with ThreadPoolExecutor(max_workers=16) as pool:
            return list(pool.map(one, jobs))

    @modal.exit()
    def stop(self):
        self.parser.close()
        self.vllm.terminate()


@app.local_entrypoint()
def main(pdf: str, pages: str = "", out: str = "sdk_out", vis: bool = False):
    """Runs on your laptop: optionally cut pages, upload, save results."""
    import json

    import pymupdf

    src = Path(pdf)
    name = src.stem
    if pages:
        # Keep only the requested pages (1-based), e.g. --pages 96,252
        nums = [int(p) for p in pages.split(",")]
        doc, small = pymupdf.open(src), pymupdf.open()
        for n in nums:
            small.insert_pdf(doc, from_page=n - 1, to_page=n - 1)
        pdf_bytes = small.tobytes()
        name = f"{name}_p{'_'.join(map(str, nums))}"
    else:
        pdf_bytes = src.read_bytes()

    print(f"Uploading {len(pdf_bytes) / 1e6:.1f} MB as '{name}' ...")
    t0 = time.time()
    res = Parser().parse_pdf.remote(pdf_bytes, name, vis)
    total_s = round(time.time() - t0, 1)

    out_dir = Path(out)
    for rel, data in res["files"].items():
        f = out_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)

    timing = {**res["timing"], "total_s": total_s}
    (out_dir / name).mkdir(parents=True, exist_ok=True)
    (out_dir / name / "timing.json").write_text(json.dumps(timing, indent=2))
    print(f"Saved {len(res['files'])} files to {out_dir / name}")
    print("Timing:", timing)
