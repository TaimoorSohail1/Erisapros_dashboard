import argparse
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--columns", type=int, default=5)
    parser.add_argument("--rows", type=int, default=4)
    args = parser.parse_args()
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    records = [record for record in catalog["records"] if record.get("preview_path")]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=15)
    cell_w, cell_h = 420, 570
    thumb_w, thumb_h = 400, 480
    per_sheet = args.columns * args.rows
    manifest = []
    for sheet_number, start in enumerate(range(0, len(records), per_sheet), start=1):
        subset = records[start:start + per_sheet]
        canvas = Image.new("RGB", (args.columns * cell_w, args.rows * cell_h), "white")
        draw = ImageDraw.Draw(canvas)
        entries = []
        for index, record in enumerate(subset):
            row, col = divmod(index, args.columns)
            x, y = col * cell_w, row * cell_h
            with Image.open(record["preview_path"]) as image:
                preview = image.convert("RGB")
                preview.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                px = x + (cell_w - preview.width) // 2
                py = y + 5
                canvas.paste(preview, (px, py))
            draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#9CA3AF", width=1)
            label = f"{start + index + 1}. {record.get('client')} | {record.get('file_name')}"
            lines = textwrap.wrap(label, width=48)[:4]
            draw.multiline_text((x + 8, y + 490), "\n".join(lines), fill="#111827", font=font, spacing=2)
            entries.append({"index": start + index + 1, "item_id": record.get("item_id"), "client": record.get("client"), "file_name": record.get("file_name")})
        path = output_dir / f"ocr_review_{sheet_number:02d}.png"
        canvas.save(path, quality=92)
        manifest.append({"sheet": sheet_number, "path": str(path), "entries": entries})
    (output_dir / "contact_sheet_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"records": len(records), "sheets": len(manifest)}))


if __name__ == "__main__":
    main()
