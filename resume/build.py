#!/usr/bin/env python3
"""Build a one-page resume PDF for a variant defined in base.json.

Usage: python3 resume/build.py <variant|all>
Variants only reorder and re-emphasise content from base.json; they never add facts.
"""
import html
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 13mm 14mm; }
* { box-sizing: border-box; }
body { font-family: Lato, Carlito, Calibri, "Helvetica Neue", Arial, sans-serif; font-size: 9.6pt;
       line-height: 1.32; color: #000; margin: 0; }
h1 { font-size: 21pt; margin: 0 0 4pt; letter-spacing: .2pt; }
h2 { font-size: 9.6pt; margin: 9pt 0 3pt; letter-spacing: .3pt; }
p { margin: 0 0 2pt; }
.head { font-weight: 700; margin-top: 4pt; }
.stack { font-size: 8.6pt; }
ul { margin: 1pt 0 3pt; padding-left: 12pt; }
li { margin-bottom: 1.5pt; }
a { color: #000; text-decoration: none; }
"""


def e(s):
    return html.escape(s).replace(" | ", " &nbsp;|&nbsp; ")


def bullets(items):
    return "<ul>" + "".join(f"<li>{e(b)}</li>" for b in items) + "</ul>"


def render(data, variant):
    v = data["variants"][variant]
    x = data["experience"]
    gh = data["github"]
    out = [f"<h1>{e(data['name'])}</h1>", f"<p>{e(data['contact'])}</p>",
           f'<p><a href="{gh}">{gh.replace("https://", "")}</a></p>', f"<p>{e(v['summary'])}</p>",
           "<h2>EXPERIENCE</h2>", f'<p class="head">{e(x["head"])}</p>',
           f'<p>{e(x["sub"])} &nbsp;|&nbsp; <a href="{x["link"]}">{e(x["link_text"])}</a></p>',
           bullets(x["bullets"]), "<h2>SELECTED PROJECTS</h2>"]
    for key in v["projects"]:
        p = data["projects"][key]
        out += [f'<p class="head">{e(p["title"])}</p>',
                f'<p class="stack">{e(p["stack"])} &nbsp;|&nbsp; <a href="{p["link"]}">GitHub</a></p>',
                bullets(p["bullets"])]
    out.append("<h2>TECHNICAL SKILLS</h2>")
    out += [f"<p><b>{e(k)}:</b> {e(data['skills'][k])}</p>" for k in v["skills"]]
    ed = data["education"]
    out += ["<h2>EDUCATION</h2>", f'<p class="head">{e(ed["head"])}</p>', f"<p>{e(ed['body'])}</p>"]
    return f"<!doctype html><meta charset='utf-8'><style>{CSS}</style>{''.join(out)}"


def build(data, variant):
    htm = HERE / f".{variant}.html"
    pdf = HERE / f"Aditya_Kumar_Singh_Resume_{variant}.pdf"
    htm.write_text(render(data, variant), encoding="utf-8")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", htm.as_uri()], check=True, capture_output=True)
    htm.unlink()
    print(pdf)


if __name__ == "__main__":
    data = json.loads((HERE / "base.json").read_text(encoding="utf-8"))
    want = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name in (data["variants"] if want == "all" else [want]):
        build(data, name)
