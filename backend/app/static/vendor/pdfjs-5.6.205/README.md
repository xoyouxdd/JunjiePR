# PDF.js 5.6.205

Mozilla PDF.js distribution, Apache-2.0; see LICENSE. Copied unchanged from the bundled workspace runtime's pdfjs-dist 5.6.205 package. Includes legacy minified browser/worker builds and the original CMaps, standard fonts and decoding resources with their upstream license notices.

Loaded on demand only when opening a learning PDF. The browser renders one page at a time; the backend sends the shared original file without per-viewer rendering. The learning reader disables PDF evaluation and WebAssembly, uses a same-origin module worker, and overlays identity watermarks in the surrounding reading frame.
