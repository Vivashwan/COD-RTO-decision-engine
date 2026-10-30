"""Render the docs to PDF (dev tool: needs `pip install markdown` and Google Chrome).

    python scripts/build_pdfs.py            # MEMO, DECISION_LOG and AI_USAGE_LOG
    python scripts/build_pdfs.py MEMO       # just one
"""
import os
import re
import subprocess
import sys

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["MEMO", "DECISION_LOG", "AI_USAGE_LOG"]
REPO = "https://github.com/Vivashwan/COD-RTO-decision-engine/blob/main/"
CSS = """
@page { size: A4; margin: 12mm 14mm; }
body { font: 9.6pt/1.38 -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 4px; } h2 { font-size: 11.5pt; margin: 11px 0 4px; border-bottom: 1px solid #ddd; padding-bottom: 2px; }
p, li { margin: 3px 0; } ul, ol { padding-left: 18px; margin: 4px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; font-size: 9pt; }
th, td { border: 1px solid #d5d5d0; padding: 3px 6px; text-align: left; vertical-align: top; }
th { background: #f2f1ed; }
a { color: #1c5cab; text-decoration: none; }
code { font-size: 8.8pt; background: #f2f1ed; padding: 0 3px; border-radius: 3px; }
"""


def render(name):
    src = os.path.join(ROOT, "docs", f"{name}.md")
    text = open(src, encoding="utf-8").read()
    # Relative repo links ("../rto/x.py") don't work inside a standalone PDF: point them at GitHub.
    text = re.sub(r"\]\(\.\./([^)#]+)(#[^)]*)?\)", lambda m: f"]({REPO}{m.group(1)}{m.group(2) or ''})", text)
    body = markdown.markdown(text, extensions=["tables"])
    title = re.search(r"^# (.+)$", text, re.M)
    html_path = os.path.join(ROOT, "docs", f"{name}.html")
    pdf_path = os.path.join(ROOT, "docs", f"{name}.pdf")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(f"<!doctype html><meta charset='utf-8'><title>{title.group(1) if title else name}</title>"
                f"<style>{CSS}</style>{body}")
    try:
        subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", f"file://{html_path}"], check=True, capture_output=True)
    finally:
        os.remove(html_path)
    return pdf_path


def main():
    for name in sys.argv[1:] or DOCS:
        print(render(name))


if __name__ == "__main__":
    main()
