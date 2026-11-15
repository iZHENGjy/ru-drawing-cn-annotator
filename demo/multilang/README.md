# Multilingual demos (synthetic data)

Two invented sheets, re-created by the scripts here:

| job | source | route | pair |
|---|---|---|---|
| `en_job/` | `en_scan.pdf` — English column-base detail as an image-only PDF (`make_demo_en_scan.py`) | scan (Tesseract `eng`, RapidOCR `EN`) | en → zh |
| `zh_job/` | `zh_text.pdf` — Chinese foundation plan with a text layer (`make_demo_zh_text.py`) | text layer | zh → en |

```bash
cd demo/multilang/en_job && python ../../../scripts/rudraw.py run
cd demo/multilang/zh_job && python ../../../scripts/rudraw.py run
```

Measured with the `claude-cli` backend (first run, nothing cached): en → zh $0.039 (3 calls),
zh → en $0.017 (1 call). Both pass every delivery gate, including the output-standard gate.
The tests run the zh → en job with the offline `fake` backend.
