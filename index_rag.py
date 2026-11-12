"""Build the local semantic cache for notes already imported into ExamAI."""

import argparse
from pathlib import Path
from shutil import copyfileobj
from urllib.request import urlopen

from database import DatabaseManager
from semantic_index import MODEL_DIR, MODEL_FILE, build_cache


MODEL_URL = ("https://huggingface.co/Xenova/multilingual-e5-small/resolve/"
             "761b726dd34fb83930e26aab4e9ac3899aa1fa78/")


def download_model():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for remote in ("tokenizer.json", f"onnx/{MODEL_FILE}"):
        target = MODEL_DIR / Path(remote).name
        if target.is_file():
            continue
        partial = target.with_suffix(".part")
        try:
            with urlopen(MODEL_URL + remote, timeout=60) as response, partial.open("wb") as file:
                copyfileobj(response, file)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("exam_assistant.db"))
    parser.add_argument("--download-model", action="store_true",
                        help="Fetch the pinned tokenizer and quantized ONNX model once")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"Database {args.db} does not exist. Import notes in ExamAI first.")
    if args.download_model:
        download_model()
    if not all((MODEL_DIR / name).is_file() for name in ("tokenizer.json", MODEL_FILE)):
        parser.error("Local model is missing. Run again with --download-model.")
    count = build_cache(DatabaseManager(args.db))
    print(f"Encoded {count} new unique passages. Restart ExamAI to load the index.")


if __name__ == "__main__":
    main()
