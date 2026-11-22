/* Reading an appliance rating sticker in the browser: barcode / QR first, then OCR.
 * Nothing is uploaded: the image stays in the browser and only the recognised text goes to /api/catalog/parse-sticker.
 * The OCR engine (Tesseract.js) and the fallback barcode reader (html5-qrcode) are loaded from a CDN only when a scan
 * actually needs them, so the rest of the page is unaffected if they cannot be loaded. */
window.WWScan = (() => {
  const TESSERACT = 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js';
  const HTML5QR = 'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js';
  const loaded = {};

  function loadScript(url) {
    return loaded[url] || (loaded[url] = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url; s.onload = resolve; s.onerror = () => { delete loaded[url]; reject(new Error('Could not load ' + url)); };
      document.head.appendChild(s);
    }));
  }

  /** Text of any barcode / QR code in the image, or '' when there is none (or no reader is available). */
  async function barcode(file) {
    try {
      if ('BarcodeDetector' in window) {
        const bmp = await createImageBitmap(file);
        const codes = await new BarcodeDetector().detect(bmp);
        return codes.map(c => c.rawValue).filter(Boolean).join('\n');
      }
      await loadScript(HTML5QR);
      const host = document.createElement('div'); host.id = 'ww-qr-host'; host.hidden = true; document.body.appendChild(host);
      try { return await new Html5Qrcode('ww-qr-host').scanFile(file, false); } finally { host.remove(); }
    } catch (e) { return ''; }  // "no code found" is normal: the caller falls back to OCR
  }

  /** OCR the sticker photo. onProgress(0..1) is called while it runs. Throws if the engine cannot load or fails. */
  async function ocr(file, onProgress) {
    await loadScript(TESSERACT);
    const r = await Tesseract.recognize(file, 'eng', { logger: m => { if (m.status === 'recognizing text' && onProgress) onProgress(m.progress); } });
    return (r.data && r.data.text) || '';
  }

  /** Live camera: start streaming into a <video>, and grab a frame as an image file. */
  async function startCamera(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error('This browser cannot open the camera. Upload a photo instead.');
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    video.srcObject = stream; await video.play();
    return stream;
  }
  function stopCamera(video) {
    const s = video.srcObject; if (s) s.getTracks().forEach(t => t.stop());
    video.srcObject = null;
  }
  function snapshot(video) {
    const c = document.createElement('canvas'); c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext('2d').drawImage(video, 0, 0);
    return new Promise((res, rej) => c.toBlob(b => b ? res(new File([b], 'sticker.jpg', { type: 'image/jpeg' })) : rej(new Error('Could not capture the photo')), 'image/jpeg', 0.92));
  }

  return { barcode, ocr, startCamera, stopCamera, snapshot };
})();
