// Matemática do zoom digital, sem DOM (testada em tests-js/).
//
// pan é normalizado: -1..1 percorre todo o espaço livre do recorte (0 = centro).

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Retângulo de origem (sx, sy, sw, sh) do vídeo `vw`×`vh` para uma saída `W`×`H` com `zoom`. */
export function cropRect(vw, vh, W, H, zoom, panX = 0, panY = 0) {
  const target = W / H;
  let sw, sh;
  if (vw / vh > target) {
    sh = vh;
    sw = vh * target;
  } else {
    sw = vw;
    sh = vw / target;
  }
  let sx = (vw - sw) / 2;
  let sy = (vh - sh) / 2;
  if (zoom > 1.001) {
    const nw = sw / zoom;
    const nh = sh / zoom;
    const px = clamp(panX, -1, 1);
    const py = clamp(panY, -1, 1);
    sx += ((sw - nw) / 2) * (1 + px);
    sy += ((sh - nh) / 2) * (1 + py);
    sw = nw;
    sh = nh;
  }
  return { sx, sy, sw, sh };
}

/** Deslocamento (em % do elemento, antes da escala) que leva a janela do recorte ao centro da prévia. */
export function previewShift(zoom, pan) {
  return zoom <= 1.001 ? 0 : ((-pan * (zoom - 1)) / (2 * zoom)) * 100;
}

/** Variação do pan para um arrasto de `dx` px num elemento de largura `width`. */
export function dragPan(dx, width, zoom, mirrored) {
  if (zoom <= 1.001 || width <= 0) return 0;
  return (mirrored ? 1 : -1) * ((2 * dx) / (width * (zoom - 1)));
}
