"""Render docs/MEMO.md to docs/MEMO.pdf (dev tool: needs `pip install markdown` and Google Chrome)."""
import os
import subprocess
import sys

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = """
@page { size: A4; margin: 12mm 14mm; }
body { font: 9.6pt/1.38 -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 4px; } h2 { font-size: 11.5pt; margin: 11px 0 4px; border-bottom: 1px solid #ddd; padding-bottom: 2px; }
p, li { margin: 3px 0; } ul, ol { padding-left: 18px; margin: 4px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 9pt; }
th, td { border: 1px solid #d5d5d0; padding: 3px 6px; text-align: left; vertical-align: top; }
th { background: #f2f1ed; }
"""


def main():
    src = os.path.join(ROOT, "docs", "MEMO.md")
    html_path = os.path.join(ROOT, "docs", "MEMO.html")
    pdf_path = os.path.join(ROOT, "docs", "MEMO.pdf")
    body = markdown.markdown(open(src, encoding="utf-8").read(), extensions=["tables"])
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>RTO memo</title><style>{CSS}</style>{body}")
    subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", f"file://{html_path}"], check=True, capture_output=True)
    os.remove(html_path)
    print(pdf_path)


if __name__ == "__main__":
    sys.exit(main())
